"""Execution engine for evaluation runs."""

import logging
import re
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
        # Record the answering model even when it came from the default, so a
        # run can always be attributed to the model that produced its answers.
        if search_type in ("rag", "agentic", "llm_only"):
            run_meta = {**run_meta, "answer_model": answer_model or get_eval_config()["answer_model"]}

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
    from ...llm import STAGE_EVAL_ANSWER, get_openai_client, record_llm_usage
    from ...config import get_eval_config

    if model is None:
        model = get_eval_config()["answer_model"]

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
    record_llm_usage(response.usage, stage=STAGE_EVAL_ANSWER, model=model, meta={"mode": "llm_only"})
    return response.choices[0].message.content.strip(), elapsed


def _rag_answer(question: str, context: str, model: Optional[str] = None):
    """Generate an answer from retrieved context using an LLM (RAG mode).

    Returns (answer_text, time_seconds).
    """
    from ...llm import STAGE_EVAL_ANSWER, get_openai_client, record_llm_usage
    from ...config import get_eval_config
    import json as _json

    if model is None:
        model = get_eval_config()["answer_model"]

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
    record_llm_usage(response.usage, stage=STAGE_EVAL_ANSWER, model=model, meta={"mode": "rag"})
    answer = response.choices[0].message.content.strip()
    return answer, elapsed


#: Tools the agentic eval exposes, in the order the MCP server registers them.
#: Graph tools are left out for now, kb_research because it is itself an agent
#: (a nested loop of its own, minutes per call), and kb_get_image because an
#: image can't be returned as a chat-completions tool result.
AGENTIC_TOOLS = ("kb_search", "kb_get")

# Loop limits come from get_eval_config() (EVAL_AGENTIC_MAX_TURNS and
# EVAL_AGENTIC_TOOL_RESULT_MAX_CHARS). The tool-result cap exists because
# kb_get returns whole documents, and a few are far beyond any context window
# (the largest is ~300M chars), so the eval client truncates -- and says so --
# until the server pages kb_get itself.

_agentic_server = None


def _get_agentic_server():
    """An in-process MCP server with the real kb-mcp tools and prompts.

    Tools are called through MCPServer.call_tool, so arguments are validated
    and results formatted exactly as for a connected client; only transport
    and auth are skipped.
    """
    global _agentic_server
    if _agentic_server is None:
        from mcp.server.mcpserver import MCPServer
        from ...server import mcp as mcp_tools
        from ...server.mcp_prompts import get_server_instructions

        server = MCPServer("kb-mcp-eval", instructions=get_server_instructions())
        for name in AGENTIC_TOOLS:
            server.tool()(getattr(mcp_tools, name))
        mcp_tools.register_prompts(server)
        _agentic_server = server
    return _agentic_server


def _agentic_answer(
    question: str,
    model: Optional[str] = None,
    source_document_id: Optional[str] = None,
    source_entry_key: Optional[str] = None,
    max_turns: Optional[int] = None,
    tool_result_max_chars: Optional[int] = None,
):
    """Answer a question the way an MCP client would, with the real kb-mcp tools.

    The model gets the server's instructions as system prompt, the server's
    research_question prompt as the task, and the tool definitions the server
    advertises, then calls tools until it answers or runs out of turns.

    Returns (final_answer, time_seconds, trace, conversation). `trace` also
    records whether the question's source document -- any file or figure of
    its entry -- showed up in any tool result ("saw") or in a kb_get result
    ("opened"); see mentions_source.
    """
    import asyncio
    import json as _json

    from ...llm import STAGE_EVAL_ANSWER, get_openai_client, record_llm_usage

    eval_config = get_eval_config()
    model = model or eval_config["answer_model"]
    max_turns = max_turns or eval_config["agentic_max_turns"]
    tool_result_max_chars = tool_result_max_chars or eval_config["agentic_tool_result_max_chars"]

    client = get_openai_client(model)
    server = _get_agentic_server()

    async def _setup():
        tools = await server.list_tools()
        prompt = await server.get_prompt("research_question", {"question": question})
        return tools, prompt

    mcp_tools, prompt = asyncio.run(_setup())
    tools = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in mcp_tools
    ]
    messages = [{"role": "system", "content": server.instructions or ""}]
    messages += [{"role": m.role, "content": m.content.text} for m in prompt.messages]

    trace = []
    saw_source = opened_source = False
    start = time.time()
    got_final_answer = False

    import openai

    msg = None
    stopped_by = None
    for _ in range(max_turns):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=16384,
            )
        except openai.BadRequestError as e:
            # Almost always the conversation outgrowing the model's context --
            # an outcome of the run (a real client would hit it too), so it is
            # recorded rather than failing the question.
            stopped_by = f"request rejected: {e}"[:500]
            break
        record_llm_usage(response.usage, stage=STAGE_EVAL_ANSWER, model=model, meta={"mode": "agentic"})
        msg = response.choices[0].message
        messages.append(msg)

        if not msg.tool_calls:
            got_final_answer = True
            break

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = _json.loads(tc.function.arguments or "{}")
                result = asyncio.run(server.call_tool(name, args))
                tool_result = "\n".join(getattr(c, "text", "") for c in result.content)
                is_error = bool(getattr(result, "is_error", False))
            except Exception as e:
                args, tool_result, is_error = tc.function.arguments, f"Error: {e}", True

            full_len = len(tool_result)
            if mentions_source(tool_result, source_document_id, source_entry_key):
                saw_source = True
                opened_source = opened_source or name == "kb_get"
            if full_len > tool_result_max_chars:
                tool_result = (
                    tool_result[:tool_result_max_chars]
                    + f"\n[[TRUNCATED BY CLIENT: showing {tool_result_max_chars:,} of {full_len:,} chars]]"
                )
            trace.append({
                "tool": name,
                "args": args,
                "result_len": full_len,
                "truncated": full_len > tool_result_max_chars,
                "is_error": is_error,
            })
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

    # Out of turns without a text answer: one more call with tools disabled.
    if not got_final_answer and stopped_by is None:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="none",
            max_tokens=16384,
        )
        record_llm_usage(response.usage, stage=STAGE_EVAL_ANSWER, model=model, meta={"mode": "agentic"})
        msg = response.choices[0].message
        messages.append(msg)

    elapsed = time.time() - start
    final_answer = (msg.content or "") if (msg is not None and stopped_by is None) else ""
    trace.append({
        "summary": True,
        "tool_calls": sum(1 for t in trace if "tool" in t),
        "hit_turn_limit": not got_final_answer and stopped_by is None,
        "stopped_by": stopped_by,
        "saw_source": saw_source,
        "opened_source": opened_source,
    })

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


_ENTRY_NUMBER = re.compile(r"^(\d+)(?:[-/_]|$)")


def document_entry_key(source_id: Optional[str], doc_id: Optional[str]) -> str:
    """Key identifying the source entry a document row belongs to.

    One DocDB entry is stored as several document rows: each attached file and
    version ("55441-..._V3_pdf", "55441/..._V2", "55441-..._V3_docx") and every
    figure and table cut from them ("55441-..._page_4_Figure_0.png"). All
    share the leading entry number, which is what a reader means by "the
    document". Doc ids without a leading number key on themselves.
    """
    doc_id = doc_id or ""
    match = _ENTRY_NUMBER.match(doc_id)
    return f"{source_id}:{match.group(1) if match else doc_id}"


_TOOL_RESULT_DOC_IDS = (
    re.compile(r'"doc_id":\s*"([^"]+)"'),  # kb_search results
    re.compile(r"^ID:\s*(\S+)", re.MULTILINE),  # kb_get / metadata headers
)


def mentions_source(
    tool_result: str,
    source_document_id: Optional[str] = None,
    source_entry_key: Optional[str] = None,
) -> bool:
    """Whether a tool result shows the question's source document.

    Matches the source's UUID, or -- since tools mostly identify documents by
    their doc id ("52728-F10266664_...") -- any doc id in the result that
    belongs to the source's entry (see document_entry_key).
    """
    if source_document_id and source_document_id in tool_result:
        return True
    if not source_entry_key:
        return False
    source_id = source_entry_key.split(":", 1)[0]
    return any(
        document_entry_key(source_id, doc_id) == source_entry_key
        for pattern in _TOOL_RESULT_DOC_IDS
        for doc_id in pattern.findall(tool_result)
    )


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

        # Entry-level hit: any row of the source's DocDB entry (another file
        # version, or a figure/table from it) counts. The exact hit above
        # under-counts whenever an entry has more than one row.
        from ..db_models import Document
        source_doc = session.get(Document, question.source_document_id)
        entry_hit_rank = None
        source_key = None
        if source_doc is not None:
            source_key = document_entry_key(source_doc.source_id, source_doc.doc_id)
            for rank, result in enumerate(search_results, start=1):
                doc = result.get("document")
                if doc and document_entry_key(doc.source_id, doc.doc_id) == source_key:
                    entry_hit_rank = rank
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
            "entry_hit_rank": entry_hit_rank,
            "source_entry_key": source_key,
            "num_retrieved": len(search_results),
            "source_document_id": question.source_document_id,
            "search_type": search_type,
        }

        judge_model = (run.judge_strategy or {}).get("model") if run.judge_strategy else None
        answer_model = (run.meta or {}).get("answer_model") or get_eval_config()["answer_model"]

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
            # Agentic: LLM answers through the real MCP tools (see _agentic_answer)
            # The agent searches with its own arguments, as a real client does:
            # the run's search filters and max_results are not imposed on it.
            agentic_opts = {k: v for k, v in (run.meta or {}).items()
                            if k in ("max_turns", "tool_result_max_chars")}
            llm_answer, llm_answer_time, agentic_trace, agentic_conversation = _agentic_answer(
                question=question.question,
                model=answer_model,
                source_document_id=question.source_document_id,
                source_entry_key=source_key,
                **agentic_opts,
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
    num_entry_hits = 0
    num_processed = 0
    total_retrieval_time = 0.0
    lock = threading.Lock()

    def _evaluate(question_id: str):
        from ...llm import usage_context

        # Each worker gets its own DB session. The usage context tags this
        # question's answer and judge calls in llm_usage with the run, so a
        # run's token cost can be read back (see `kb eval compare`).
        with usage_context(eval_run_id=run_snapshot.id, eval_question_id=question_id):
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
                        if (result.meta or {}).get("entry_hit_rank") is not None:
                            num_entry_hits += 1
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
        "num_entry_hits": num_entry_hits,
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
