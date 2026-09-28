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
    dry_run: bool = False,
) -> List[Dict]:
    """Run every (generation, search type, answer model, judge) combination.

    Retrieval-only search types ignore `answer_models` and run once per
    generation and judge. Each judge is a separate run, so answers are
    regenerated per judge.

    Args:
        run_meta: Extra run meta for answer-mode runs (e.g. agentic loop limits).
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

    cells = []
    for gid in generation_ids:
        for search_type in search_types:
            models = answer_models if search_type in ANSWER_SEARCH_TYPES else [None]
            for answer_model in models:
                for judge_model in judge_models:
                    name = grid_run_name(prefix, search_type, labels[gid], answer_model, judge_model)
                    cells.append((name, gid, search_type, answer_model, judge_model))

    outcomes = []
    for i, (name, gid, search_type, answer_model, judge_model) in enumerate(cells, 1):
        with get_db_session() as session:
            done = _run_has_results(session, name)
        if done or dry_run:
            status = "skipped" if done else "planned"
            logger.info(f"[{i}/{len(cells)}] {status}: {name}")
            outcomes.append({"name": name, "status": status})
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
        )
        outcomes.append({"name": name, "status": "ran", **stats})
    return outcomes


def _agentic_summary(meta: Dict) -> Optional[Dict]:
    for entry in reversed((meta or {}).get("agentic_trace") or []):
        if isinstance(entry, dict) and entry.get("summary"):
            return entry
    return None


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
        query = session.query(EvalRun)
        if name_prefix:
            query = query.filter(EvalRun.name.like(f"{name_prefix}%"))
        if run_ids:
            query = query.filter(EvalRun.id.in_(list(run_ids)))
        runs = query.order_by(EvalRun.name, EvalRun.created_time).all()

        for run in runs:
            results = session.query(EvalResult).filter_by(run_id=run.id).all()
            judged = [r for r in results if r.is_judge_hit is not None]
            summaries = [s for s in (_agentic_summary(r.meta) for r in results) if s]
            answer_times = [
                (r.meta or {}).get("llm_answer_time") for r in results
                if (r.meta or {}).get("llm_answer_time") is not None
            ]
            tokens = dict(
                session.query(LLMUsage.stage, func.sum(LLMUsage.total_tokens))
                .filter(LLMUsage.meta["eval_run_id"].as_string() == run.id)
                .group_by(LLMUsage.stage)
                .all()
            )
            rows.append({
                "run_id": run.id,
                "name": run.name,
                "search_type": run.search_type,
                "answer_model": (run.meta or {}).get("answer_model"),
                "judge_model": (run.judge_strategy or {}).get("model"),
                "questions": len(results),
                "exact_hits": sum(1 for r in results if r.is_hit),
                # None for runs made before the entry-level metric existed.
                "entry_hits": (
                    sum(1 for r in results if (r.meta or {}).get("entry_hit_rank") is not None)
                    if any("entry_hit_rank" in (r.meta or {}) for r in results) else None
                ),
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
