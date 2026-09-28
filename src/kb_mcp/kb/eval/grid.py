"""Run a grid of evaluation runs and compare runs side by side.

A grid is every combination of question set (generation), search type,
answering model and judge. Each run is named from what it tested, so the
names alone tell runs apart, and a grid re-run skips every run that already
has results under its name -- an interrupted grid resumes, and adding one
model to a finished grid only runs the new cells.
"""

import logging
from typing import Dict, Iterable, List, Optional

from .db_models import EvalGeneration, EvalResult, EvalRun
from ..database import get_db_session

logger = logging.getLogger(__name__)

#: Search types where an LLM writes the answer (and --answer-model applies).
ANSWER_SEARCH_TYPES = ("rag", "agentic", "llm_only")


def model_label(model: Optional[str]) -> str:
    """Short, name-safe label for a model id ("argo:claude-sonnet-5" -> "claude-sonnet-5")."""
    if not model:
        return "default"
    return model.removeprefix("argo:").replace(":", "-").replace("/", "-")


def generation_label(generation: EvalGeneration) -> str:
    """Label a question set by the model that wrote it, else by its id."""
    model = (generation.meta or {}).get("model")
    return model_label(model) if model else generation.id[:8]


def grid_run_name(
    prefix: str,
    search_type: str,
    generation: str,
    answer_model: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> str:
    """`<prefix>-<search type>[-<answer model>]-q<question set>[-judge-<judge>]`."""
    parts = [prefix, search_type]
    if search_type in ANSWER_SEARCH_TYPES:
        parts.append(model_label(answer_model))
    parts.append(f"q{generation}")
    if judge_model:
        parts += ["judge", model_label(judge_model)]
    return "-".join(parts)


def _run_has_results(session, name: str) -> bool:
    return (
        session.query(EvalResult.id)
        .join(EvalRun, EvalRun.id == EvalResult.run_id)
        .filter(EvalRun.name == name)
        .first()
        is not None
    )


def run_grid(
    prefix: str,
    generation_ids: Iterable[str],
    search_types: Iterable[str],
    answer_models: Iterable[Optional[str]] = (None,),
    judge_models: Iterable[Optional[str]] = (None,),
    max_results: int = 10,
    workers: int = 1,
    run_meta: Optional[Dict] = None,
    audit_filters: Optional[Dict] = None,
    dry_run: bool = False,
) -> List[Dict]:
    """Run every (generation, search type, answer model, judge) combination.

    Retrieval-only search types ignore `answer_models` and run once per
    generation and judge. In answer modes each judge is a separate run, but
    only one of them generates answers; the others re-judge it (see
    kb.eval.rejudge), so a second judge costs judge calls only.

    Args:
        run_meta: Extra run meta for answer-mode runs (e.g. agentic loop limits).
        audit_filters: Which questions to use (see get_eval_questions); the CLI
            passes {"strict": True}. None uses every question.
        dry_run: Only report what would run.

    Returns:
        One dict per cell: name, status ("ran", "skipped" or "planned"), and
        the run stats for cells that ran.
    """
    from .runner import eval as run_eval

    generation_ids = list(generation_ids)
    answer_models = list(answer_models) or [None]
    judge_models = list(judge_models) or [None]

    with get_db_session() as session:
        labels = {}
        for gid in generation_ids:
            generation = session.get(EvalGeneration, gid)
            if generation is None:
                raise ValueError(f"Generation not found: {gid}")
            labels[gid] = generation_label(generation)

    # One cell per run. In answer modes, the judges of one (question set,
    # search type, answer model) share answers: one run generates them -- an
    # existing run under any of the judges, else the first judge's -- and every
    # other judge re-judges that run instead of answering again.
    cells = []
    with get_db_session() as session:
        for gid in generation_ids:
            for search_type in search_types:
                models = answer_models if search_type in ANSWER_SEARCH_TYPES else [None]
                for answer_model in models:
                    names = [grid_run_name(prefix, search_type, labels[gid], answer_model, j) for j in judge_models]
                    answers_from = None
                    if search_type in ANSWER_SEARCH_TYPES and len(judge_models) > 1:
                        answers_from = next((n for n in names if _run_has_results(session, n)), names[0])
                    for name, judge_model in zip(names, judge_models):
                        source = answers_from if answers_from and answers_from != name else None
                        cells.append((name, gid, search_type, answer_model, judge_model, source))

    outcomes = []
    for i, (name, gid, search_type, answer_model, judge_model, answers_from) in enumerate(cells, 1):
        with get_db_session() as session:
            done = _run_has_results(session, name)
        how = f"re-judge {answers_from}" if answers_from else "run"
        if done or dry_run:
            status = "skipped" if done else "planned"
            logger.info(f"[{i}/{len(cells)}] {status} ({how}): {name}")
            outcomes.append({"name": name, "status": status, "how": how})
            continue

        if answers_from:
            from .rejudge import rejudge_run

            with get_db_session() as session:
                source = session.query(EvalRun).filter_by(name=answers_from).order_by(EvalRun.created_time.desc()).first()
            if source is None:
                raise RuntimeError(f"Answer run {answers_from} missing; cannot re-judge for {name}")
            logger.info(f"[{i}/{len(cells)}] re-judging {answers_from} as: {name}")
            stats = rejudge_run(source.id, judge_model, name=name, workers=workers)
            outcomes.append({"name": name, "status": "ran", "how": how, **stats})
            continue

        logger.info(f"[{i}/{len(cells)}] running: {name}")
        stats = run_eval(
            name=name,
            generation_id=gid,
            search_type=search_type,
            max_results=max_results,
            answer_model=answer_model if search_type in ANSWER_SEARCH_TYPES else None,
            judge_strategy={"enabled": True, "model": judge_model} if judge_model else None,
            use_llm_judge=judge_model is not None,
            workers=workers,
            meta=run_meta if search_type == "agentic" else None,
            audit_filters=audit_filters,
        )
        outcomes.append({"name": name, "status": "ran", "how": how, **stats})
    return outcomes


def _agentic_summary(trace) -> Optional[Dict]:
    """The summary entry the agentic loop appends to its trace, if any."""
    for entry in reversed(trace or []):
        if isinstance(entry, dict) and entry.get("summary"):
            return entry
    return None


def _select_runs(session, name_prefix: Optional[str], run_ids: Optional[Iterable[str]]):
    query = session.query(EvalRun)
    if name_prefix:
        query = query.filter(EvalRun.name.like(f"{name_prefix}%"))
    if run_ids:
        query = query.filter(EvalRun.id.in_(list(run_ids)))
    return query.order_by(EvalRun.name, EvalRun.created_time).all()


def _result_rows(session, run_id: str):
    """Per-result fields compare needs, read as JSON paths.

    Loading whole results would pull every stored agent conversation (up to
    hundreds of kB each); these paths keep a large grid's comparison cheap.
    """
    meta = EvalResult.meta
    return (
        session.query(
            EvalResult.id,
            EvalResult.question_id,
            EvalResult.is_hit,
            EvalResult.is_judge_hit,
            meta["source_entry_key"].as_string().label("source_entry_key"),
            meta["entry_hit_rank"].as_string().label("entry_hit_rank"),
            meta["llm_answer_time"].as_float().label("answer_time"),
            meta["agentic_trace"].label("trace"),
        )
        .filter(EvalResult.run_id == run_id)
        .all()
    )


def compare_runs(name_prefix: Optional[str] = None, run_ids: Optional[Iterable[str]] = None) -> List[Dict]:
    """Side-by-side metrics for runs selected by name prefix and/or id.

    Per run: exact and entry-level retrieval hits, judge verdicts, and for
    agentic runs how often the agent saw/opened the source, hit the turn
    limit or was stopped by the endpoint; plus answer time and the answer
    and judge tokens recorded in llm_usage under the run (runs made before
    usage was tagged with the run show no tokens).
    """
    from sqlalchemy import func

    from ..db_models import LLMUsage
    from ...llm import STAGE_EVAL_ANSWER, STAGE_EVAL_JUDGE

    rows = []
    with get_db_session() as session:
        for run in _select_runs(session, name_prefix, run_ids):
            results = _result_rows(session, run.id)
            judged = [r for r in results if r.is_judge_hit is not None]
            summaries = [s for s in (_agentic_summary(r.trace) for r in results) if s]
            answer_times = [r.answer_time for r in results if r.answer_time is not None]
            tokens = dict(
                session.query(LLMUsage.stage, func.sum(LLMUsage.total_tokens))
                .filter(LLMUsage.meta["eval_run_id"].as_string() == run.id)
                .group_by(LLMUsage.stage)
                .all()
            )
            # The entry-level metric exists only for runs that recorded a source
            # entry key; older runs show None rather than a misleading zero.
            has_entry_metric = any(r.source_entry_key for r in results)
            rows.append({
                "run_id": run.id,
                "name": run.name,
                "generation_id": run.generation_id,
                "search_type": run.search_type,
                "answer_model": (run.meta or {}).get("answer_model"),
                "judge_model": (run.judge_strategy or {}).get("model"),
                "created_time": run.created_time,
                "questions": len(results),
                "exact_hits": sum(1 for r in results if r.is_hit),
                "entry_hits": sum(1 for r in results if r.entry_hit_rank) if has_entry_metric else None,
                "judged": len(judged),
                "judge_correct": sum(1 for r in judged if r.is_judge_hit),
                "agent_saw_source": sum(1 for s in summaries if s.get("saw_source")) if summaries else None,
                "agent_opened_source": sum(1 for s in summaries if s.get("opened_source")) if summaries else None,
                "turn_limit": sum(1 for s in summaries if s.get("hit_turn_limit")) if summaries else None,
                "stopped": sum(1 for s in summaries if s.get("stopped_by")) if summaries else None,
                "avg_answer_seconds": sum(answer_times) / len(answer_times) if answer_times else None,
                "answer_tokens": int(tokens.get(STAGE_EVAL_ANSWER) or 0) or None,
                "judge_tokens": int(tokens.get(STAGE_EVAL_JUDGE) or 0) or None,
            })
    return rows


def per_question_matrix(name_prefix: Optional[str] = None, run_ids: Optional[Iterable[str]] = None) -> List[Dict]:
    """Question-by-run verdicts, one block per question set.

    A cell is the judge's verdict when the run was judged, else whether the
    source's entry was retrieved. Rows every run gets wrong usually point at
    a bad question; rows only one model gets right show where models differ.

    Returns:
        One dict per generation: generation id/name, the runs (id, name) in
        column order, and rows of (question id, question text, cells), where a
        cell is {"result_id", "ok"} or None when the run has no result for it.
    """
    from .db_models import EvalDataset

    blocks = []
    with get_db_session() as session:
        runs = _select_runs(session, name_prefix, run_ids)
        by_generation: Dict[str, List[EvalRun]] = {}
        for run in runs:
            by_generation.setdefault(run.generation_id, []).append(run)

        for gid, gen_runs in by_generation.items():
            generation = session.get(EvalGeneration, gid) if gid else None
            cells: Dict[str, Dict[str, Dict]] = {}
            for run in gen_runs:
                for r in _result_rows(session, run.id):
                    ok = r.is_judge_hit if r.is_judge_hit is not None else bool(r.entry_hit_rank or r.is_hit)
                    cells.setdefault(r.question_id, {})[run.id] = {"result_id": r.id, "ok": ok}
            questions = (
                session.query(EvalDataset.id, EvalDataset.question)
                .filter(EvalDataset.id.in_(list(cells)))
                .all()
            ) if cells else []
            rows = []
            for qid, text in questions:
                row_cells = [cells[qid].get(run.id) for run in gen_runs]
                rows.append({"question_id": qid, "question": text, "cells": row_cells})
            # Hardest questions first: fewest runs got them right.
            rows.sort(key=lambda row: sum(1 for c in row["cells"] if c and c["ok"]))
            blocks.append({
                "generation_id": gid,
                "generation_name": (generation.name if generation else None) or (gid or "")[:8],
                "runs": [{"run_id": run.id, "name": run.name} for run in gen_runs],
                "rows": rows,
            })
    return blocks


def run_groups(limit: int = 50) -> List[Dict]:
    """Run name prefixes (the part before "-<search type>-"), newest first.

    Grid runs share a prefix, so each group is one grid's worth of runs.
    """
    groups: Dict[str, Dict] = {}
    with get_db_session() as session:
        for name, search_type, created in session.query(EvalRun.name, EvalRun.search_type, EvalRun.created_time):
            if not name or not search_type:
                continue
            marker = f"-{search_type}-"
            prefix = name.split(marker, 1)[0] if marker in name else None
            if not prefix:
                continue
            group = groups.setdefault(prefix, {"prefix": prefix, "runs": 0, "latest": created})
            group["runs"] += 1
            if created and (group["latest"] is None or created > group["latest"]):
                group["latest"] = created
    ordered = sorted(groups.values(), key=lambda g: g["latest"] or 0, reverse=True)
    return ordered[:limit]
