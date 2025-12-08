"""Simple metrics computation for evaluation runs."""

import logging
from typing import Dict, Optional

from sqlalchemy import func

from .core import EvalResult, EvalRun
from ..database import get_db_session

logger = logging.getLogger(__name__)


def compute_hit_rate(
    run_id: Optional[str] = None,
    generation_id: Optional[str] = None,
    use_judge: bool = False,
    session=None,
) -> float:
    """Compute hit rate for a run or generation.

    Hit rate = (Number of hits) / (Total questions with is_hit set)

    Normalizes with respect to questions that actually have is_hit set (not NULL),
    excluding questions that haven't been evaluated yet.

    Args:
        run_id: Run ID to compute metric for (optional if generation_id provided)
        generation_id: Generation ID to compute metric for latest run (optional if run_id provided)
        use_judge: Whether to use LLM judge results instead of exact matches
        session: Database session

    Returns:
        float: Hit rate (0.0 to 1.0)

    Example:
        >>> hit_rate = compute_hit_rate(run_id="run-123")
        >>> print(f"Hit rate: {hit_rate:.2%}")
        >>> # Or by generation_id (uses latest run)
        >>> hit_rate = compute_hit_rate(generation_id="gen-123")
        >>> # Or as positional argument (tries as run_id first, then generation_id)
        >>> hit_rate = compute_hit_rate("gen-123")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Support positional argument: if only run_id is provided as positional, 
        # try it as run_id first, then as generation_id if no results found
        if run_id and not generation_id:
            # Check if it's actually a run_id by looking for results
            result_count = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id
            ).scalar()
            
            # If no results found, try as generation_id
            if result_count == 0:
                logger.debug(f"No results found for run_id {run_id}, trying as generation_id")
                generation_id = run_id
                run_id = None
        
        # If generation_id provided, find the latest run for that generation
        if generation_id and not run_id:
            latest_run = session.query(EvalRun).filter(
                EvalRun.generation_id == generation_id
            ).order_by(EvalRun.created_time.desc()).first()
            
            if not latest_run:
                logger.warning(f"No runs found for generation {generation_id}")
                return 0.0
            
            run_id = latest_run.id
            logger.debug(f"Using latest run {run_id} for generation {generation_id}")
        
        if not run_id:
            raise ValueError("Either run_id or generation_id must be provided")
        
        # Count hits
        if use_judge:
            hits = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_judge_hit == True,  # noqa: E712
            ).scalar()
            # Get total number of questions with is_judge_hit set (not NULL)
            total = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_judge_hit.isnot(None),
            ).scalar()
        else:
            hits = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_hit == True,  # noqa: E712
            ).scalar()
            # Get total number of questions with is_hit set (not NULL)
            total = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_hit.isnot(None),
            ).scalar()

        if total == 0:
            return 0.0

        return hits / total

    finally:
        if own_session:
            session.__exit__(None, None, None)


def get_rank_distribution(
    run_id: str,
    use_judge: bool = False,
    session=None,
) -> Dict[int, int]:
    """Get distribution of hit ranks.

    Args:
        run_id: Run ID to analyze
        use_judge: Whether to use LLM judge results
        session: Database session

    Returns:
        Dict mapping rank to count (only hits included)

    Example:
        >>> dist = get_rank_distribution("run-123")
        >>> print(f"Rank 1: {dist.get(1, 0)} hits")
        >>> print(f"Rank 2: {dist.get(2, 0)} hits")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        if use_judge:
            results = session.query(EvalResult.hit_rank, func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_judge_hit == True  # noqa: E712
            ).group_by(EvalResult.hit_rank).all()
        else:
            results = session.query(EvalResult.hit_rank, func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_hit == True  # noqa: E712
            ).group_by(EvalResult.hit_rank).all()

        distribution = {rank: count for rank, count in results if rank is not None}
        return distribution

    finally:
        if own_session:
            session.__exit__(None, None, None)


def get_summary_stats(
    run_id: str,
    use_judge: bool = False,
    session=None,
) -> Dict:
    """Get summary statistics for a run.

    Args:
        run_id: Run ID to analyze
        use_judge: Whether to use LLM judge results
        session: Database session

    Returns:
        Dict with summary stats:
        {
            "run_id": str,
            "total_questions": int,
            "hits": int,
            "misses": int,
            "hit_rate": float,
            "rank_distribution": {1: count, 2: count, ...},
        }

    Example:
        >>> stats = get_summary_stats("run-123")
        >>> print(f"Hit rate: {stats['hit_rate']:.2%}")
        >>> print(f"Rank 1 hits: {stats['rank_distribution'].get(1, 0)}")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get hits
        if use_judge:
            hits = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_judge_hit == True,  # noqa: E712
            ).scalar()
            # Get total number of questions with is_judge_hit set (not NULL)
            total = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_judge_hit.isnot(None),
            ).scalar()
        else:
            hits = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_hit == True,  # noqa: E712
            ).scalar()
            # Get total number of questions with is_hit set (not NULL)
            total = session.query(func.count(EvalResult.id)).filter(
                EvalResult.run_id == run_id,
                EvalResult.is_hit.isnot(None),
            ).scalar()

        misses = total - hits
        hit_rate = hits / total if total > 0 else 0.0

        # Get rank distribution
        rank_dist = get_rank_distribution(run_id, use_judge=use_judge, session=session)

        return {
            "run_id": run_id,
            "total_questions": total,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "rank_distribution": rank_dist,
        }

    finally:
        if own_session:
            session.__exit__(None, None, None)
