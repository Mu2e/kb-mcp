"""Execution engine for evaluation runs."""

import logging
import socket
import time
from typing import Dict, List, Optional

from tqdm import tqdm

from .core import EvalRun, EvalResult, EvalRetrievedDocument, get_eval_questions
from ..database import get_db_session
from ..search.search import search
from ..embedding.utils import get_embedding_name
from ...eval.judge import llm_judge_answer

logger = logging.getLogger(__name__)


def create_eval_run(
    name: Optional[str] = None,
    description: Optional[str] = None,
    generation_id: Optional[str] = None,
    audit_filters: Optional[Dict] = None,
    embedding_name: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    max_results: int = 10,
    search_filters: Optional[Dict] = None,
    judge_strategy: Optional[Dict] = None,
    meta: Optional[Dict] = None,
    session=None,
) -> EvalRun:
    """Create an evaluation run configuration.

    Args:
        name: Optional name for this run
        description: Optional description
        generation_id: Optional filter to questions from specific generation
        audit_filters: Optional audit criteria (e.g., {"is_valid": True, "audit_type": "llm_judge"})
        embedding_name: Embedding model to use for search
        chunking_strategy: Optional chunking strategy identifier
        max_results: Maximum number of results to retrieve per query
        search_filters: Optional document filters for search
        judge_strategy: Optional LLM judge configuration (e.g., {"enabled": True, "model": "gpt-4"})
        meta: Optional metadata dict
        session: Database session

    Returns:
        EvalRun: Created run configuration

    Example:
        >>> run = create_eval_run(
        ...     name="Test embeddings v2",
        ...     generation_id="gen-123",
        ...     audit_filters={"is_valid": True},
        ...     max_results=10
        ... )
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get default embedding name if not provided
        if embedding_name is None:
            embedding_name = get_embedding_name(session=session)

        run = EvalRun(
            name=name,
            description=description,
            generation_id=generation_id,
            audit_filters=audit_filters or {},
            embedding_name=embedding_name,
            chunking_strategy=chunking_strategy,
            max_results=max_results,
            search_filters=search_filters or {},
            judge_strategy=judge_strategy,
            meta=meta or {},
        )
        session.add(run)
        session.commit()

        # Refresh and expunge if we own the session
        if own_session:
            session.refresh(run)
            session.expunge(run)

        logger.info(f"Created eval run: {run.id} (name={name})")
        return run

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error creating eval run: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


def evaluate_single_question(
    run: EvalRun,
    question_id: str,
    use_llm_judge: bool = False,
    session=None,
) -> EvalResult:
    """Evaluate a single question using run configuration.

    Args:
        run: EvalRun configuration object
        question_id: Question ID to evaluate
        use_llm_judge: Whether to run LLM judge (requires judge_strategy in run config)
        session: Database session

    Returns:
        EvalResult: Created result record

    Raises:
        ValueError: If question not found or has no source_document_id
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get question
        question = get_eval_questions(question_id=question_id, session=session)
        if not question:
            raise ValueError(f"Question not found: {question_id}")

        if not question.source_document_id:
            raise ValueError(f"Question {question_id} has no source_document_id")

        # Perform search using run configuration
        start_time = time.time()
        search_response = search(
            query=question.question,
            embedding_name=run.embedding_name,
            max_results=run.max_results,
            filter=run.search_filters or {},
            session=session,
        )
        retrieval_time = time.time() - start_time

        # Extract results list from search response
        search_results = search_response.get("results", [])

        # Check if source document is in results
        is_hit = False
        hit_rank = None
        best_similarity = None

        for rank, result in enumerate(search_results, start=1):
            # Result structure: {"document": Document, "chunks": [...]}
            doc = result.get("document")
            if doc and doc.id == question.source_document_id:
                is_hit = True
                hit_rank = rank
                # Get best similarity from chunks
                chunks = result.get("chunks", [])
                if chunks:
                    best_similarity = chunks[0].get("similarity")
                break

        # If not found, get best similarity from top result
        if not is_hit and search_results:
            top_result = search_results[0]
            chunks = top_result.get("chunks", [])
            if chunks:
                best_similarity = chunks[0].get("similarity")

        # Initialize judge fields
        is_judge_hit = None
        justification = None
        judge_time_seconds = None

        # Run LLM judge if requested and configured
        if use_llm_judge and run.judge_strategy and run.judge_strategy.get("enabled"):
            # Get retrieved context from top result
            if search_results:
                top_result = search_results[0]
                top_doc = top_result.get("document")
                if top_doc and top_doc.text:
                    # Truncate if too long
                    max_context_chars = 8000
                    context = top_doc.text[:max_context_chars]
                    if len(top_doc.text) > max_context_chars:
                        context += "... (truncated)"

                    # Run LLM judge
                    judge_model = run.judge_strategy.get("model")
                    judge_result = llm_judge_answer(
                        question=question.question,
                        retrieved_context=context,
                        expected_answer=question.answer,
                        model=judge_model,
                    )

                    is_judge_hit = judge_result["is_hit"]
                    justification = judge_result["justification"]
                    judge_time_seconds = judge_result["time_seconds"]

        # Create result record
        result = EvalResult(
            run_id=run.id,
            question_id=question_id,
            is_hit=is_hit,
            hit_rank=hit_rank,
            is_judge_hit=is_judge_hit,
            justification=justification,
            judge_time_seconds=judge_time_seconds,
            retrieval_time_seconds=retrieval_time,
            hostname=socket.gethostname(),
            best_similarity=best_similarity,
            meta={
                "num_retrieved": len(search_results),
                "source_document_id": question.source_document_id,
            },
        )
        session.add(result)
        session.flush()  # Get result ID

        # Create retrieved document records
        for rank, search_result in enumerate(search_results, start=1):
            # Result structure: {"document": Document, "chunks": [...]}
            doc = search_result.get("document")
            chunks = search_result.get("chunks", [])
            
            if doc:
                # Get best similarity from chunks
                similarity = chunks[0].get("similarity") if chunks else None
                chunk_ids = [chunk.get("chunk_id") for chunk in chunks if chunk.get("chunk_id")]
                
                retrieved_doc = EvalRetrievedDocument(
                    result_id=result.id,
                    document_id=doc.id,
                    rank=rank,
                    similarity=similarity,
                    chunk_ids=chunk_ids if chunk_ids else None,
                )
                session.add(retrieved_doc)

        session.commit()

        # Refresh and expunge if we own the session
        if own_session:
            session.refresh(result)
            session.expunge(result)

        logger.debug(
            f"Evaluated question {question_id}: is_hit={is_hit}, "
            f"hit_rank={hit_rank}, retrieval_time={retrieval_time:.3f}s"
        )

        return result

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error evaluating question {question_id}: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


def execute_eval_run(
    run_id: str,
    use_llm_judge: bool = False,
    session=None,
) -> Dict:
    """Execute an evaluation run.

    Retrieves questions based on run configuration, evaluates each,
    and stores results.

    Args:
        run_id: Run ID to execute
        use_llm_judge: Whether to run LLM judge (requires judge_strategy in run config)
        session: Database session

    Returns:
        Dict with execution statistics:
        {
            "run_id": str,
            "num_questions": int,
            "num_hits": int,
            "total_time_seconds": float,
            "avg_retrieval_time_seconds": float,
        }

    Raises:
        ValueError: If run not found

    Example:
        >>> run = create_eval_run(...)
        >>> stats = execute_eval_run(run.id)
        >>> print(f"Hit rate: {stats['num_hits'] / stats['num_questions']:.2%}")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get run configuration
        run = session.query(EvalRun).filter_by(id=run_id).first()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        logger.info(f"Executing eval run {run_id} (name={run.name})")

        # Get questions to evaluate
        questions = get_eval_questions(
            generation_id=run.generation_id,
            audit_filter=run.audit_filters if run.audit_filters else None,
            session=session,
        )

        if not questions:
            logger.warning(f"No questions found for run {run_id}")
            return {
                "run_id": run_id,
                "num_questions": 0,
                "num_hits": 0,
                "total_time_seconds": 0.0,
                "avg_retrieval_time_seconds": 0.0,
            }

        logger.info(f"Found {len(questions)} questions to evaluate")

        # Evaluate each question
        start_time = time.time()
        num_hits = 0
        num_processed = 0
        total_retrieval_time = 0.0

        # Use tqdm for progress indication
        question_iterator = tqdm(
            questions,
            desc="Evaluating questions",
            unit="question",
            total=len(questions)
        )

        for question in question_iterator:
            if not question.source_document_id:
                question_iterator.set_postfix_str("Skipping (no source doc)", refresh=False)
                logger.warning(
                    f"Skipping question {question.id} (no source_document_id)"
                )
                continue

            try:
                result = evaluate_single_question(
                    run=run,
                    question_id=question.id,
                    use_llm_judge=use_llm_judge,
                    session=session,
                )

                num_processed += 1
                if result.is_hit:
                    num_hits += 1

                if result.retrieval_time_seconds:
                    total_retrieval_time += result.retrieval_time_seconds

                # Update progress bar with current hit rate
                hit_rate = num_hits / num_processed if num_processed > 0 else 0.0
                question_iterator.set_postfix_str(
                    f"Hit rate: {hit_rate:.1%} ({num_hits}/{num_processed})",
                    refresh=False
                )

            except Exception as e:
                question_iterator.set_postfix_str(f"Error: {str(e)[:30]}...", refresh=False)
                logger.error(f"Failed to evaluate question {question.id}: {e}")
                # Continue with other questions
                continue

        total_time = time.time() - start_time
        avg_retrieval_time = (
            total_retrieval_time / num_processed if num_processed > 0 else 0.0
        )

        stats = {
            "run_id": run_id,
            "num_questions": num_processed,
            "num_hits": num_hits,
            "total_time_seconds": total_time,
            "avg_retrieval_time_seconds": avg_retrieval_time,
        }

        hit_rate = num_hits / num_processed if num_processed > 0 else 0.0
        logger.info(
            f"Completed eval run {run_id}: {num_hits}/{num_processed} hits "
            f"({hit_rate:.2%}) in {total_time:.1f}s"
        )

        return stats

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error executing eval run: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


def eval(
    name: Optional[str] = None,
    description: Optional[str] = None,
    generation_id: Optional[str] = None,
    audit_filters: Optional[Dict] = None,
    embedding_name: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    max_results: int = 10,
    search_filters: Optional[Dict] = None,
    judge_strategy: Optional[Dict] = None,
    use_llm_judge: bool = False,
    meta: Optional[Dict] = None,
    session=None,
) -> Dict:
    """Create and execute an evaluation run in one step.

    This is the main entry point for running evaluations. It creates
    a run configuration and immediately executes it.

    Args:
        name: Optional name for this run
        description: Optional description
        generation_id: Optional filter to questions from specific generation
        audit_filters: Optional audit criteria (e.g., {"is_valid": True})
        embedding_name: Embedding model to use for search
        chunking_strategy: Optional chunking strategy identifier
        max_results: Maximum number of results to retrieve per query
        search_filters: Optional document filters for search
        judge_strategy: Optional LLM judge configuration (e.g., {"enabled": True, "model": "gpt-4"})
        use_llm_judge: Whether to run LLM judge (requires judge_strategy)
        meta: Optional metadata dict
        session: Database session

    Returns:
        Dict with execution statistics including run_id

    Example:
        >>> stats = eval(
        ...     name="Test new embeddings",
        ...     generation_id="gen-123",
        ...     audit_filters={"is_valid": True},
        ...     max_results=10
        ... )
        >>> print(f"Run {stats['run_id']}: {stats['num_hits']}/{stats['num_questions']} hits")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Create run configuration
        run = create_eval_run(
            name=name,
            description=description,
            generation_id=generation_id,
            audit_filters=audit_filters,
            embedding_name=embedding_name,
            chunking_strategy=chunking_strategy,
            max_results=max_results,
            search_filters=search_filters,
            judge_strategy=judge_strategy,
            meta=meta,
            session=session,
        )

        # Execute the run
        stats = execute_eval_run(
            run_id=run.id,
            use_llm_judge=use_llm_judge,
            session=session,
        )

        return stats

    finally:
        if own_session:
            session.__exit__(None, None, None)


def get_run_results(
    run_id: str,
    is_hit: Optional[bool] = None,
    session=None,
) -> List[EvalResult]:
    """Get results for a run.

    Args:
        run_id: Run ID to get results for
        is_hit: Optional filter by hit status
        session: Database session

    Returns:
        List[EvalResult]: List of results

    Example:
        >>> results = get_run_results(run_id="abc-123", is_hit=False)
        >>> print(f"Found {len(results)} misses")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        query = session.query(EvalResult).filter_by(run_id=run_id)

        if is_hit is not None:
            query = query.filter_by(is_hit=is_hit)

        results = query.order_by(EvalResult.created_time).all()

        if own_session and results:
            for result in results:
                session.refresh(result)
                session.expunge(result)

        return results

    finally:
        if own_session:
            session.__exit__(None, None, None)
