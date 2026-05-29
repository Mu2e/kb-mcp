"""Simple metrics computation for evaluation runs."""

import logging
from typing import Dict, Optional

from sqlalchemy import func, case, select

from .db_models import EvalResult, EvalRun, EvalDataset, EvalAudit
from ..database import get_db_session
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
        ```python
        hit_rate = compute_hit_rate(run_id="run-123")
        print(f"Hit rate: {hit_rate:.2%}")
        # Or by generation_id (uses latest run)
        hit_rate = compute_hit_rate(generation_id="gen-123")
        # Or as positional argument (tries as run_id first, then generation_id)
        hit_rate = compute_hit_rate("gen-123")
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
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



def get_rank_distribution(
    run_id: str,
    use_judge: bool = False,
    audit_type: Optional[str] = None,
    session=None,
) -> Dict[int, int]:
    """Get distribution of hit ranks.

    Args:
        run_id: Run ID to analyze
        use_judge: Whether to use LLM judge results
        audit_type: Optional filter by audit type
        session: Database session

    Returns:
        Dict mapping rank to count (only hits included)

    Example:
        ```python
        dist = get_rank_distribution("run-123")
        print(f"Rank 1: {dist.get(1, 0)} hits")
        print(f"Rank 2: {dist.get(2, 0)} hits")
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        # Build base filter
        base_filter = EvalResult.run_id == run_id
        
        # Apply audit filter if specified
        if audit_type:
            audited_question_ids = select(EvalAudit.question_id).where(
                EvalAudit.audit_type == audit_type,
                EvalAudit.is_valid == True  # noqa: E712
            ).distinct()
            base_filter = base_filter & (EvalResult.question_id.in_(audited_question_ids))
        
        # Filter by run and hit type
        if use_judge:
            results = session.query(EvalResult.hit_rank, func.count(EvalResult.id)).filter(
                base_filter,
                EvalResult.is_judge_hit == True  # noqa: E712
            ).group_by(EvalResult.hit_rank).all()
        else:
            results = session.query(EvalResult.hit_rank, func.count(EvalResult.id)).filter(
                base_filter,
                EvalResult.is_hit == True  # noqa: E712
            ).group_by(EvalResult.hit_rank).all()

        distribution = {rank: count for rank, count in results if rank is not None}
        return distribution



def get_summary_stats(
    run_id: str,
    use_judge: bool = False,
    audit_type: Optional[str] = None,
    session=None,
) -> Dict:
    """Get summary statistics for a run.

    Computes both document match and LLM judge stats in a single query for efficiency.

    Args:
        run_id: Run ID to analyze
        use_judge: Whether to use LLM judge results (for backward compatibility)
        audit_type: Optional filter by audit type (e.g., 'llm_judge', 'human_review')
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
            "judge_total_questions": int,  # Only if judge results exist
            "judge_hits": int,
            "judge_misses": int,
            "judge_hit_rate": float,
            "judge_rank_distribution": {1: count, 2: count, ...},
        }

    Example:
        ```python
        stats = get_summary_stats("run-123")
        print(f"Doc hit rate: {stats['hit_rate']:.2%}")
        print(f"Judge hit rate: {stats.get('judge_hit_rate', 0):.2%}")
        # Filter by audit type
        stats = get_summary_stats("run-123", audit_type="llm_judge")
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        # Build base filter
        base_filter = EvalResult.run_id == run_id

        # Check run's search type — llm_only has no retrieval so doc stats are meaningless
        run = session.query(EvalRun).filter_by(id=run_id).first()
        is_llm_only = run and getattr(run, "search_type", None) == "llm_only"

        # Apply audit filter if specified - use subquery to get valid question IDs
        if audit_type:
            audited_question_ids = select(EvalAudit.question_id).where(
                EvalAudit.audit_type == audit_type,
                EvalAudit.is_valid == True  # noqa: E712
            ).distinct()
            base_filter = base_filter & (EvalResult.question_id.in_(audited_question_ids))

        # Compute both document match and judge stats in one pass using aggregation
        # Skip retrieval stats for llm_only runs (is_hit is always False there, not meaningful)
        if is_llm_only:
            doc_hits = 0
            doc_total = 0
        else:
            # Count document hits
            doc_hits = session.query(func.count(EvalResult.id)).filter(
                base_filter,
                EvalResult.is_hit == True  # noqa: E712
            ).scalar() or 0

            # Count document total (with is_hit set)
            doc_total = session.query(func.count(EvalResult.id)).filter(
                base_filter,
                EvalResult.is_hit.isnot(None)
            ).scalar() or 0
        
        # Count judge hits
        judge_hits = session.query(func.count(EvalResult.id)).filter(
            base_filter,
            EvalResult.is_judge_hit == True  # noqa: E712
        ).scalar() or 0
        
        # Count judge total (with is_judge_hit set)
        judge_total = session.query(func.count(EvalResult.id)).filter(
            base_filter,
            EvalResult.is_judge_hit.isnot(None)
        ).scalar() or 0
        
        # For backward compatibility, use the requested metric
        if use_judge:
            hits = judge_hits
            total = judge_total
        else:
            hits = doc_hits
            total = doc_total
        
        misses = total - hits
        hit_rate = hits / total if total > 0 else 0.0
        
        # Get rank distributions
        rank_dist = get_rank_distribution(run_id, use_judge=False, audit_type=audit_type, session=session)
        judge_rank_dist = get_rank_distribution(run_id, use_judge=True, audit_type=audit_type, session=session) if judge_total > 0 else {}
        
        result = {
            "run_id": run_id,
            "total_questions": total,
            "hits": hits,
            "misses": misses,
            "hit_rate": hit_rate,
            "rank_distribution": rank_dist,
        }
        
        # Add judge stats if available
        if judge_total > 0:
            result.update({
                "judge_total_questions": judge_total,
                "judge_hits": judge_hits,
                "judge_misses": judge_total - judge_hits,
                "judge_hit_rate": judge_hits / judge_total if judge_total > 0 else 0.0,
                "judge_rank_distribution": judge_rank_dist,
            })
        
        return result

