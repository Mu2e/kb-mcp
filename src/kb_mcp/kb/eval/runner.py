"""Execution engine for evaluation runs."""

import logging
import socket
import time
from typing import Dict, List, Optional

from tqdm import tqdm

from .db_models import EvalRun, EvalResult, EvalRetrievedDocument, get_eval_questions
from ..search.search import search
from ..embedding.utils import get_embedding_name
from ...eval_utils.judge import llm_judge_answer
from ...config import get_eval_config
from ..database import get_db_session

logger = logging.getLogger(__name__)


def create_eval_run(
    name: Optional[str] = None,
    description: Optional[str] = None,
    generation_id: Optional[str] = None,
    audit_filters: Optional[Dict] = None,
    search_type: str = "semantic",
    embedding_name: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    max_results: int = 10,
    search_filters: Optional[Dict] = None,
    answer_model: Optional[str] = None,
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
        ```python
        run = create_eval_run(
            name="Test embeddings v2",
            generation_id="gen-123",
            audit_filters={"is_valid": True},
            max_results=10
        )
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        # Get default embedding name if not provided
        if embedding_name is None:
            embedding_name = get_embedding_name(session=session)

        run_meta = meta or {}
        if answer_model:
            run_meta = {**run_meta, "answer_model": answer_model}

        run = EvalRun(
            name=name,
            description=description,
            generation_id=generation_id,
            audit_filters=audit_filters or {},
            search_type=search_type,
            embedding_name=embedding_name,
            chunking_strategy=chunking_strategy,
            max_results=max_results,
            search_filters=search_filters or {},
            judge_strategy=judge_strategy,
            meta=run_meta,
        )
        session.add(run)
        session.flush()  # Always flush to get the ID, regardless of session ownership
        session.refresh(run)  # Refresh to ensure ID is loaded

        logger.info(f"Created eval run: {run.id} (name={name})")
        return run


def _llm_only_answer(question: str, model: Optional[str] = None):
    """Generate an answer using only the LLM with no retrieval context (baseline).

    Returns (answer_text, time_seconds).
    """
    from ...llm import get_openai_client
    from ...config import get_eval_config

    if model is None:
        model = get_eval_config().get("judge_model")

    client = get_openai_client(model)
    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a precise scientific assistant specializing in high-energy physics experiments."},
            {"role": "user", "content": question},
        ],
        max_tokens=16384,
    )
    elapsed = time.time() - start
    return response.choices[0].message.content.strip(), elapsed


def _rag_answer(question: str, context: str, model: Optional[str] = None):
    """Generate an answer from retrieved context using an LLM (RAG mode).

    Returns (answer_text, time_seconds).
    """
    from ...llm import get_openai_client
    from ...config import get_eval_config
    import json as _json

    if model is None:
        model = get_eval_config().get("judge_model")

    client = get_openai_client(model)
    prompt = (
        f"Answer the following question using only the provided context. "
        f"Be concise and precise.\n\n"
        f"Question: {question}\n\n"
        f"Context:\n{context}\n\n"
        f"Answer:"
    )
    start = time.time()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a precise scientific assistant. Answer only from the provided context."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=16384,
    )
    elapsed = time.time() - start
    answer = response.choices[0].message.content.strip()
    return answer, elapsed


def _agentic_answer(
    question: str,
    source_id: Optional[str] = None,
    parser_id: Optional[str] = None,
    es_filter: Optional[Dict] = None,
    max_results: int = 5,
    model: Optional[str] = None,
):
    """Run the research_question agentic loop with kb_search/kb_get as tools.

    The LLM can call kb_search and kb_get iteratively, mirroring the MCP
    research_question prompt used in the web interface.

    Returns (final_answer, time_seconds, trace).
    """
    from ...llm import get_openai_client
    from ...config import get_eval_config
    from ..search.search import search as kb_search_fn
    from ..documents import get as kb_get_fn
    import json as _json

    if model is None:
        model = get_eval_config().get("judge_model")

    client = get_openai_client(model)

    filter_str = None  # unused, kept for reference

    tools = [
        {
            "type": "function",
            "function": {
                "name": "kb_search",
                "description": "Search the knowledge base. Returns ranked documents with excerpts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "default": max_results},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kb_get",
                "description": "Retrieve the full text of a document by its ID.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_id": {"type": "string", "description": "Document ID"},
                    },
                    "required": ["doc_id"],
                },
            },
        },
    ]

    system_prompt = (
        "You are a scientific research assistant helping to answer questions about "
        "legacy high-energy physics experiments. Use kb_search to find relevant documents "
        "and kb_get to read full document content when needed. "
        "After gathering information, provide a precise, concise answer."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": (
            f"Research the following question using the knowledge base:\n\n"
            f"Question: {question}\n\n"
            f"Steps:\n"
            f"1. Use kb_search to find relevant documents\n"
            f"2. Use kb_get on the most relevant document(s) if needed\n"
            f"3. Provide a precise answer citing the sources"
        )},
    ]

    trace = []
    start = time.time()
    max_iterations = 5
    got_final_answer = False

    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=16384,
        )
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            # LLM gave a final text answer
            got_final_answer = True
            break

        # Execute tool calls
        for tc in msg.tool_calls:
            fn = tc.function.name
            args = _json.loads(tc.function.arguments)
            trace.append({"tool": fn, "args": args})

            if fn == "kb_search":
                search_response = kb_search_fn(
                    query=args["query"],
                    max_results=args.get("max_results", max_results),
                    source_id=source_id,
                    parser_id=parser_id,
                    filter=es_filter,
                )
                results = search_response.get("results", [])
                tool_result = _json.dumps([
                    {"doc_id": r["document"].doc_id, "excerpt": (r["document"].text or "")[:500]}
                    for r in results if r.get("document")
                ])
            elif fn == "kb_get":
                doc = kb_get_fn(identifier=args["doc_id"])
                if isinstance(doc, list):
                    doc = next((d for d in doc if d.parser_id == "marker"), doc[0] if doc else None)
                tool_result = doc.text[:4000] if doc and doc.text else "Document not found"
            else:
                tool_result = "Unknown tool"

            trace.append({"tool_result_len": len(tool_result)})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result,
            })

    # If loop exhausted max_iterations without a text turn, force a synthesis call
    if not got_final_answer:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tool_choice="none",
            max_tokens=16384,
        )
        msg = response.choices[0].message
        messages.append(msg)

    elapsed = time.time() - start
    final_answer = msg.content or ""

    # Serialize full message history for storage
    conversation = []
    for m in messages:
        if hasattr(m, "model_dump"):
            entry = m.model_dump(exclude_none=True)
        elif hasattr(m, "to_dict"):
            entry = m.to_dict()
        else:
            entry = m  # already a plain dict (tool result messages)
        conversation.append(entry)

    return final_answer, elapsed, trace, conversation


def evaluate_single_question(
    run: EvalRun,
    question_id: str,
    use_llm_judge: bool = False,
    rerank: Optional[bool] = None,
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
    should_close = session is None

    with get_db_session(session) as session:
        # Get question
        question = get_eval_questions(question_id=question_id, session=session)
        if not question:
            raise ValueError(f"Question not found: {question_id}")

        if not question.source_document_id:
            raise ValueError(f"Question {question_id} has no source_document_id")

        # Perform search using run configuration
        from ..search.search_fulltext import search_fulltext
        from ..search.search import search_semantic
        search_type = getattr(run, "search_type", "semantic") or "semantic"

        # Extract first-class filter params from search_filters so they map to
        # dedicated SQL columns rather than going through the ES filter parser.
        raw_filters = run.search_filters or {}
        source_id_filter = raw_filters.get("source_id")
        parser_id_filter = raw_filters.get("parser_id")
        es_filter = {k: v for k, v in raw_filters.items() if k not in ("source_id", "parser_id")} or None

        start_time = time.time()
        if search_type == "llm_only":
            search_response = {"results": []}
        elif search_type == "fulltext":
            search_response = search_fulltext(
                query=question.question,
                max_results=run.max_results,
                source_id=source_id_filter,
                parser_id=parser_id_filter,
                filter=es_filter,
                session=session,
            )
        elif search_type in ("rag", "agentic"):
            # Both use semantic retrieval as the base; answer generation handled below
            search_response = search_semantic(
                query=question.question,
                embedding_name=run.embedding_name,
                chunking_strategy=run.chunking_strategy,
                max_results=run.max_results,
                source_id=source_id_filter,
                parser_id=parser_id_filter,
                filter=es_filter,
                session=session,
            )
        else:  # "semantic" or "hybrid" (default)
            search_response = search(
                query=question.question,
                embedding_name=run.embedding_name,
                chunking_strategy=run.chunking_strategy,
                max_results=run.max_results,
                source_id=source_id_filter,
                parser_id=parser_id_filter,
                filter=es_filter,
                rerank=rerank,
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

        # Initialize judge / answer fields
        is_judge_hit = None
        justification = None
        judge_time_seconds = None
        llm_answer = None
        result_meta = {
            "num_retrieved": len(search_results),
            "source_document_id": question.source_document_id,
            "search_type": search_type,
        }

        judge_model = (run.judge_strategy or {}).get("model") if run.judge_strategy else None
        answer_model = (run.meta or {}).get("answer_model") or get_eval_config().get("gen_model")

        def _build_context(results, max_chars=720000):
            """Concatenate top retrieved doc texts into a single context string."""
            docs = [(r.get("document"), r) for r in results if r.get("document") and r.get("document").text]
            if not docs:
                return ""
            per_doc = max_chars // len(docs)
            parts = []
            for doc, _ in docs:
                snippet = doc.text[:per_doc]
                parts.append(f"[{doc.doc_id or doc.id}]\n{snippet}")
            return "\n\n---\n\n".join(parts)

        if search_type == "llm_only":
            llm_answer, llm_answer_time = _llm_only_answer(
                question=question.question,
                model=answer_model,
            )
            result_meta["llm_answer"] = llm_answer
            result_meta["llm_answer_time"] = llm_answer_time

            # Always judge in llm_only mode — it's the only scoring signal available
            judge_result = llm_judge_answer(
                question=question.question,
                retrieved_context=llm_answer,
                expected_answer=question.answer,
                model=judge_model,
                mode="answer",
            )
            is_judge_hit = judge_result["is_hit"]
            justification = judge_result["justification"]
            judge_time_seconds = judge_result["time_seconds"]

        elif search_type in ("semantic", "fulltext", "hybrid"):
            # Retrieval-only: optionally judge whether context contains the answer
            if use_llm_judge and run.judge_strategy and run.judge_strategy.get("enabled") and search_results:
                context = _build_context(search_results)
                judge_result = llm_judge_answer(
                    question=question.question,
                    retrieved_context=context,
                    expected_answer=question.answer,
                    model=judge_model,
                )
                is_judge_hit = judge_result["is_hit"]
                justification = judge_result["justification"]
                judge_time_seconds = judge_result["time_seconds"]

        elif search_type == "rag":
            # RAG: LLM generates an answer from retrieved context, then judge scores it
            if search_results:
                context = _build_context(search_results)
                llm_answer, llm_answer_time = _rag_answer(
                    question=question.question,
                    context=context,
                    model=answer_model,
                )
                result_meta["llm_answer"] = llm_answer
                result_meta["llm_answer_time"] = llm_answer_time

                if use_llm_judge and run.judge_strategy and run.judge_strategy.get("enabled"):
                    judge_result = llm_judge_answer(
                        question=question.question,
                        retrieved_context=llm_answer,
                        expected_answer=question.answer,
                        model=judge_model,
                        mode="answer",
                    )
                    is_judge_hit = judge_result["is_hit"]
                    justification = judge_result["justification"]
                    judge_time_seconds = judge_result["time_seconds"]

        elif search_type == "agentic":
            # Agentic: LLM runs the research_question loop with kb_search/kb_get tools
            llm_answer, llm_answer_time, agentic_trace, agentic_conversation = _agentic_answer(
                question=question.question,
                source_id=source_id_filter,
                parser_id=parser_id_filter,
                es_filter=es_filter,
                max_results=run.max_results,
                model=answer_model,
            )
            result_meta["llm_answer"] = llm_answer
            result_meta["llm_answer_time"] = llm_answer_time
            result_meta["agentic_trace"] = agentic_trace
            result_meta["agentic_conversation"] = agentic_conversation

            if use_llm_judge and run.judge_strategy and run.judge_strategy.get("enabled"):
                judge_result = llm_judge_answer(
                    question=question.question,
                    retrieved_context=llm_answer,
                    expected_answer=question.answer,
                    model=judge_model,
                    mode="answer",
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
            meta=result_meta,
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

        # Refresh if we own the session
        if should_close:
            session.refresh(result)

        logger.debug(
            f"Evaluated question {question_id}: is_hit={is_hit}, "
            f"hit_rank={hit_rank}, retrieval_time={retrieval_time:.3f}s"
        )

        return result


def execute_eval_run(
    run_id: str,
    use_llm_judge: bool = False,
    workers: int = 1,
    rerank: Optional[bool] = None,
    session=None,
) -> Dict:
    """Execute an evaluation run.

    Retrieves questions based on run configuration, evaluates each,
    and stores results.

    Args:
        run_id: Run ID to execute
        use_llm_judge: Whether to run LLM judge (requires judge_strategy in run config)
        workers: Number of parallel workers (default 1)
        rerank: Whether to apply cross-encoder reranking during retrieval
            (True/False; None reads the RERANKER_ENABLED config default)
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
        ```python
        run = create_eval_run(...)
        stats = execute_eval_run(run.id)
        print(f"Hit rate: {stats['num_hits'] / stats['num_questions']:.2%}")
        ```
    """
    import threading

    # Load run config and question IDs in a single session, then close it.
    # Workers each open their own session so SQLAlchemy objects are not shared
    # across threads.
    with get_db_session(session) as db:
        run = db.query(EvalRun).filter_by(id=run_id).first()
        if not run:
            raise ValueError(f"Run not found: {run_id}")

        logger.info(f"Executing eval run {run_id} (name={run.name})")

        questions = get_eval_questions(
            generation_id=run.generation_id,
            audit_filter=run.audit_filters if run.audit_filters else None,
            session=db,
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

        # Snapshot everything needed by workers before closing the session
        question_ids = [q.id for q in questions if q.source_document_id]
        skipped = len(questions) - len(question_ids)
        if skipped:
            logger.warning(f"Skipping {skipped} question(s) with no source_document_id")

        # Detach a plain-dict copy of run config for workers to read without a session
        run_snapshot = EvalRun(
            id=run.id,
            name=run.name,
            generation_id=run.generation_id,
            audit_filters=run.audit_filters,
            search_type=run.search_type,
            embedding_name=run.embedding_name,
            chunking_strategy=run.chunking_strategy,
            max_results=run.max_results,
            search_filters=run.search_filters,
            judge_strategy=run.judge_strategy,
            meta=run.meta,
        )

    logger.info(f"Found {len(question_ids)} questions to evaluate (workers={workers})")

    start_time = time.time()
    num_hits = 0
    num_processed = 0
    total_retrieval_time = 0.0
    lock = threading.Lock()

    def _evaluate(question_id: str):
        # Each worker gets its own DB session
        result = evaluate_single_question(
            run=run_snapshot,
            question_id=question_id,
            use_llm_judge=use_llm_judge,
            rerank=rerank,
            session=None,
        )
        return result

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with tqdm(total=len(question_ids), desc="Evaluating questions", unit="question") as pbar:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_evaluate, qid): qid for qid in question_ids}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    with lock:
                        num_processed += 1
                        if result.is_hit:
                            num_hits += 1
                        if result.retrieval_time_seconds:
                            total_retrieval_time += result.retrieval_time_seconds
                        hit_rate = num_hits / num_processed if num_processed > 0 else 0.0
                        pbar.set_postfix_str(
                            f"Hit rate: {hit_rate:.1%} ({num_hits}/{num_processed})",
                            refresh=False,
                        )
                except Exception as e:
                    logger.error(f"Failed to evaluate question {futures[future]}: {e}")
                finally:
                    pbar.update(1)

    total_time = time.time() - start_time
    avg_retrieval_time = total_retrieval_time / num_processed if num_processed > 0 else 0.0

    hit_rate = num_hits / num_processed if num_processed > 0 else 0.0
    logger.info(
        f"Completed eval run {run_id}: {num_hits}/{num_processed} hits "
        f"({hit_rate:.2%}) in {total_time:.1f}s"
    )

    return {
        "run_id": run_id,
        "num_questions": num_processed,
        "num_hits": num_hits,
        "total_time_seconds": total_time,
        "avg_retrieval_time_seconds": avg_retrieval_time,
    }


def eval(
    name: Optional[str] = None,
    description: Optional[str] = None,
    generation_id: Optional[str] = None,
    audit_filters: Optional[Dict] = None,
    search_type: str = "semantic",
    embedding_name: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    max_results: int = 10,
    search_filters: Optional[Dict] = None,
    answer_model: Optional[str] = None,
    judge_strategy: Optional[Dict] = None,
    use_llm_judge: bool = False,
    workers: int = 1,
    rerank: Optional[bool] = None,
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
        workers: Number of parallel workers (default 1)
        rerank: Whether to apply cross-encoder reranking during retrieval
            (True/False; None reads the RERANKER_ENABLED config default)
        meta: Optional metadata dict
        session: Database session

    Returns:
        Dict with execution statistics including run_id

    Example:
        ```python
        stats = eval(
            name="Test new embeddings",
            generation_id="gen-123",
            audit_filters={"is_valid": True},
            max_results=10
        )
        print(f"Run {stats['run_id']}: {stats['num_hits']}/{stats['num_questions']} hits")
        ```
    """
    # Create and commit the run record in its own session so workers can see it
    with get_db_session(session) as db:
        run = create_eval_run(
            name=name,
            description=description,
            generation_id=generation_id,
            audit_filters=audit_filters,
            search_type=search_type,
            embedding_name=embedding_name,
            chunking_strategy=chunking_strategy,
            max_results=max_results,
            search_filters=search_filters,
            answer_model=answer_model,
            judge_strategy=judge_strategy,
            meta=meta,
            session=db,
        )
        run_id = run.id

    # Execute with no shared session — each worker (and the main thread) opens its own
    stats = execute_eval_run(
        run_id=run_id,
        use_llm_judge=use_llm_judge,
        workers=workers,
        rerank=rerank,
        session=None,
    )

    return stats


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
        ```python
        results = get_run_results(run_id="abc-123", is_hit=False)
        print(f"Found {len(results)} misses")
        ```
    """
    with get_db_session(session) as session:
        query = session.query(EvalResult).filter_by(run_id=run_id)

        if is_hit is not None:
            query = query.filter_by(is_hit=is_hit)

        results = query.order_by(EvalResult.created_time).all()

        return results
