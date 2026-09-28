"""Judge an existing run's answers again, with another judge model.

Answering is by far the expensive part of an answer-mode run (an agentic
answer is ~40-160k tokens, a verdict ~1-2k), so comparing judges should not
regenerate answers. A re-judged run is a new run with the source run's
configuration and answers and only the judge's fields redone; its meta names
the source run, and each result names the result whose answer it judged.
"""

import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional

from tqdm import tqdm

from .db_models import EvalDataset, EvalResult, EvalRun
from ..database import get_db_session
from ...eval_utils.judge import llm_judge_answer

logger = logging.getLogger(__name__)

#: Search types whose results carry an answer that can be judged on its own.
REJUDGEABLE_SEARCH_TYPES = ("rag", "agentic", "llm_only")

#: Result meta copied to the re-judged result. The agent conversation is left
#: out (it can be hundreds of kB per answer); `answer_result_id` links to it.
_COPIED_META = (
    "llm_answer", "llm_answer_time", "agentic_trace", "entry_hit_rank",
    "source_entry_key", "source_document_id", "num_retrieved", "search_type",
)


def rejudge_run(
    source_run_id: str,
    judge_model: str,
    name: Optional[str] = None,
    workers: int = 1,
) -> Dict:
    """Create a run that re-judges `source_run_id`'s answers with `judge_model`.

    Args:
        source_run_id: Run whose answers are judged again.
        judge_model: Judge for the new run.
        name: Name for the new run (default: the source's name with its judge
            suffix replaced, e.g. "...-judge-gpt-5.5" -> "...-judge-claude-opus-5").
        workers: Parallel judge calls.

    Returns:
        Dict with run_id, num_questions, num_judge_hits, total_time_seconds.
    """
    import time

    from .grid import model_label
    from ...llm import usage_context

    start = time.time()
    with get_db_session() as session:
        source = session.get(EvalRun, source_run_id)
        if source is None:
            raise ValueError(f"Run not found: {source_run_id}")
        if source.search_type not in REJUDGEABLE_SEARCH_TYPES:
            raise ValueError(
                f"Run {source_run_id} is a '{source.search_type}' run; only answer-mode runs "
                f"({', '.join(REJUDGEABLE_SEARCH_TYPES)}) can be re-judged"
            )
        if name is None:
            base = (source.name or f"run-{source.id[:8]}").split("-judge-", 1)[0]
            name = f"{base}-judge-{model_label(judge_model)}"

        run = EvalRun(
            name=name,
            description=f"Re-judged answers of run {source.id} with {judge_model}",
            generation_id=source.generation_id,
            audit_filters=source.audit_filters,
            search_type=source.search_type,
            embedding_name=source.embedding_name,
            chunking_strategy=source.chunking_strategy,
            max_results=source.max_results,
            search_filters=source.search_filters,
            judge_strategy={"enabled": True, "model": judge_model},
            meta={**(source.meta or {}), "rejudged_from_run_id": source.id},
        )
        session.add(run)
        session.commit()
        run_id = run.id

        # Plain tuples for the workers, which use their own sessions.
        items = [
            (r.id, r.question_id, r.is_hit, r.hit_rank, r.retrieval_time_seconds, r.best_similarity,
             {k: (r.meta or {}).get(k) for k in _COPIED_META if k in (r.meta or {})})
            for r in session.query(EvalResult).filter_by(run_id=source.id).all()
        ]

    def _judge(item):
        result_id, question_id, is_hit, hit_rank, retrieval_time, best_similarity, meta = item
        with get_db_session() as session:
            question = session.get(EvalDataset, question_id)
            answer = meta.get("llm_answer")
            verdict = None
            if answer is not None:
                with usage_context(eval_run_id=run_id, eval_question_id=question_id):
                    verdict = llm_judge_answer(
                        question=question.question,
                        retrieved_context=answer,
                        expected_answer=question.answer,
                        model=judge_model,
                        mode="answer",
                    )
            session.add(EvalResult(
                run_id=run_id,
                question_id=question_id,
                is_hit=is_hit,
                hit_rank=hit_rank,
                is_judge_hit=verdict["is_hit"] if verdict else None,
                justification=verdict["justification"] if verdict else None,
                judge_time_seconds=verdict["time_seconds"] if verdict else None,
                retrieval_time_seconds=retrieval_time,
                hostname=socket.gethostname(),
                best_similarity=best_similarity,
                meta={**meta, "answer_result_id": result_id},
            ))
            session.commit()
            return bool(verdict and verdict["is_hit"])

    hits = done = 0
    lock = threading.Lock()
    with tqdm(total=len(items), desc="Re-judging", unit="answer") as pbar:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_judge, item) for item in items]
            for future in as_completed(futures):
                try:
                    ok = future.result()
                    with lock:
                        done += 1
                        hits += ok
                        pbar.set_postfix_str(f"Judged correct: {hits}/{done}", refresh=False)
                except Exception as e:
                    logger.error(f"Re-judging failed for one answer: {e}")
                finally:
                    pbar.update(1)

    return {
        "run_id": run_id,
        "name": name,
        "num_questions": done,
        "num_judge_hits": hits,
        "total_time_seconds": time.time() - start,
    }
