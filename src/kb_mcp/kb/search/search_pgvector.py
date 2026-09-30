"""PostgreSQL/pgvector search implementation."""

import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import text

from ..db_models import Document

logger = logging.getLogger(__name__)


def _search_pgvector(
    session,
    embedding_table,
    query_embedding: List[float],
    max_results: int,
    source_id: Optional[str],
    doc_type: Optional[str],
    chunking_strategy: Optional[str],
    parser_id: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    explain_analyse: bool = False,
    embedding_time: float = 0.0,
    start_time: Optional[float] = None,
    max_chunks_per_doc: Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Helper for PostgreSQL + pgvector search.

    Runs the optimized CTE-based similarity query using the pgvector `<=>`
    operator, then groups results by document and returns the same
    structure as `search()`.
    """
    # Derive embedding_name from table name (strip "embeddings_" prefix)
    embedding_name = embedding_table.name.replace("embeddings_", "", 1) if embedding_table.name.startswith("embeddings_") else embedding_table.name
    
    # Start timing if not provided
    if start_time is None:
        start_time = time.time()

    # Convert query embedding to PostgreSQL vector format
    query_vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build WHERE clause using unified helper
    from .filters import build_where_clause
    where_clause_sql, filter_params_dict = build_where_clause(
        source_id=source_id,
        doc_type=doc_type,
        chunking_strategy=chunking_strategy,
        parser_id=parser_id,
        filter=filter,
        skip_kwargs={"session", "explain_analyse", "embedding_name", "max_results"},
        **kwargs
    )

    # Get search configuration
    from ...config import get_search_config
    search_config = get_search_config()

    # Get more chunks initially to account for deduplication
    # Set high enough for vector index performance, low enough to avoid memory issues
    initial_limit = max_results * search_config['initial_limit_multiplier']

    # Limit chunks per document for diversity (allow override via parameter)
    if max_chunks_per_doc is None:
        max_chunks_per_doc = search_config['max_chunks_per_doc']

    def _empty(**extra) -> Dict[str, Any]:
        return {
            "results": [],
            "metadata": {
                "time_search_total": time.time() - start_time,
                "time_embedding": embedding_time,
                "total_results": 0,
                "embedding_name": embedding_name,
                "max_results": max_results,
                **extra,
            },
        }

    # A bound on every statement below. Measured 2026-09-30: a source_id
    # filter that no embedded chunk passes (mu2e-wiki -- none of the 477,500
    # chunks in embeddings_st_bgesmallenv1_5) made the iterative index scan
    # below read the whole index, one scalar subquery per row: 203 s and
    # still no rows, longer than an MCP client waits in silence. A search
    # that runs out of time fails loudly (search_hybrid keeps its full-text
    # half); it never hangs. SEARCH_VECTOR_TIMEOUT_MS, 0 = no bound.
    timeout_ms = int(search_config['vector_timeout_ms'])
    if timeout_ms > 0:
        session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))

    # How many embedded chunks can pass the filters? Counted up to one past
    # SEARCH_EXACT_MAX_CHUNKS, so a broad filter stops early; the join starts
    # from the filtered documents, so a narrow one is cheap to count too.
    #  - none: there is nothing to rank, so the vector query is skipped.
    #  - up to SEARCH_EXACT_MAX_CHUNKS: rank them exactly (`filtered` below).
    #    An exact ranking of all 477,500 chunks took 1.0 s (2026-09-30), so a
    #    subset of that size costs a fraction of it, and it is exact.
    #  - more: the index path, where a filter most chunks pass lets the
    #    iterative scan stop early.
    vector_path = "index"
    filtered_chunks = None
    if where_clause_sql != "TRUE":
        exact_max = int(search_config['exact_max_chunks'])
        filtered_chunks = session.execute(
            text(f"""
                SELECT count(*) FROM (
                    SELECT 1
                    FROM {embedding_table.name} e
                    JOIN chunks c ON c.id = e.chunk_id
                    JOIN documents d ON c.document_id = d.id
                    WHERE {where_clause_sql}
                    LIMIT :count_cap
                ) passing
            """),
            {"count_cap": exact_max + 1, **filter_params_dict},
        ).scalar()
        if filtered_chunks == 0:
            if timeout_ms > 0:
                session.execute(text("SET LOCAL statement_timeout = DEFAULT"))
            return _empty(vector_path="no_match", filtered_chunks=0)
        if filtered_chunks <= exact_max:
            vector_path = "exact"

    # Nearest neighbours first, per-document diversity second. The ORDER BY
    # distance LIMIT in `knn` is what lets the vector index do the work. The
    # filters are a per-row scalar subquery rather than a join on purpose:
    # with a join the planner may (and at LIMIT 2000 did) pick a plan that
    # filters documents first and computes every distance. A scalar subquery
    # cannot be turned into a join, so the index scan is the only way to
    # satisfy ORDER BY ... LIMIT, and the iterative scan keeps reading it
    # until enough rows pass the filters.
    # Ranking chunks within their document (ROW_NUMBER) before that LIMIT, as
    # this query once did, forces a distance for every chunk that passes the
    # filters -- ~390k for doc_type=text -- and never touches the index.
    #
    # Ranking inside the top candidates gives the same ranks as ranking all
    # chunks: every chunk of a document that is closer than a candidate is a
    # candidate too. So only the index's approximation can change results.
    if vector_path == "exact":
        # The subset is small, so filter first and compute every distance:
        # the MATERIALIZED `filtered` keeps the index out of the ORDER BY.
        knn_ctes = f"""
        filtered AS MATERIALIZED (
            SELECT e.chunk_id, e.embedding
            FROM {embedding_table.name} e
            JOIN chunks c ON c.id = e.chunk_id
            JOIN documents d ON c.document_id = d.id
            WHERE {where_clause_sql}
        ),
        knn AS MATERIALIZED (
            SELECT
                f.chunk_id,
                (f.embedding <=> CAST(:query_embedding AS vector)) AS distance
            FROM filtered f
            ORDER BY distance
            LIMIT :initial_limit
        )"""
    else:
        knn_ctes = f"""
        knn AS MATERIALIZED (
            SELECT
                e.chunk_id,
                (e.embedding <=> CAST(:query_embedding AS vector)) AS distance
            FROM {embedding_table.name} e
            WHERE (
                SELECT TRUE
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
                WHERE c.id = e.chunk_id AND {where_clause_sql}
                LIMIT 1
            )
            ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
            LIMIT :initial_limit
        )"""
    vector_query = f"""
        WITH {knn_ctes},
        nearest AS (
            SELECT
                c.id AS chunk_id,
                c.document_id,
                c.chunk_index,
                c.chunk_strategy,
                c.char_start_index,
                c.char_end_index,
                c.token_length,
                c.section_path,
                k.distance
            FROM knn k
            JOIN chunks c ON c.id = k.chunk_id
        ),
        base_candidates AS (
            SELECT
                n.*,
                (1 - n.distance) AS score,
                ROW_NUMBER() OVER (PARTITION BY n.document_id ORDER BY n.distance) AS rank_in_doc
            FROM nearest n
        ),
        diverse_chunks AS (
            SELECT *
            FROM base_candidates
            WHERE rank_in_doc <= :max_chunks_per_doc
        ),
        top_documents AS (
            SELECT document_id
            FROM diverse_chunks
            GROUP BY document_id
            ORDER BY MIN(distance) ASC
            LIMIT :max_results
        )
        SELECT
            dc.chunk_id,
            dc.document_id,
            dc.chunk_index,
            dc.chunk_strategy,
            dc.char_start_index,
            dc.char_end_index,
            dc.token_length,
            dc.section_path,
            dc.score,
            dc.rank_in_doc
        FROM diverse_chunks dc
        WHERE dc.document_id IN (SELECT document_id FROM top_documents)
        ORDER BY dc.score DESC
    """

    # Planner settings for this query only. SET LOCAL ends with the
    # transaction; a plain SET would stay on the pooled connection and change
    # the plans of every later, unrelated query that reuses it.
    #  - ivfflat.probes: lists searched per query. IVFFlat is approximate;
    #    measured 2026-09-24 on 40 logged queries against the exact scan, with
    #    451 lists: 32 probes -> 81% top-20 agreement, 128 -> 98% (0.56 s),
    #    200 -> identical (1.3 s). SEARCH_IVFFLAT_PROBES.
    #  - ivfflat.iterative_scan: when filters (doc_type, source, metadata)
    #    reject candidates, keep reading the index instead of returning fewer
    #    than LIMIT rows (pgvector >= 0.8). relaxed_order is fine: the ranking
    #    below recomputes order from the distances.
    #  - ivfflat.max_probes: how far that iterative scan may go (pgvector >=
    #    0.8; by default, every list). With only broad filters reaching this
    #    path it rarely matters, but it bounds a filter that passes many
    #    chunks none of which are near the query. SEARCH_IVFFLAT_MAX_PROBES.
    #  - work_mem: room for the sorts, which otherwise spill to disk.
    # The exact path computes every distance on purpose, so it takes only
    # work_mem: the index settings, and enable_seqscan = off, are for the
    # index scan.
    if vector_path == "exact":
        settings = ("SET LOCAL work_mem = '64MB'",)
    else:
        settings = (
            f"SET LOCAL ivfflat.probes = {int(search_config['ivfflat_probes'])}",
            "SET LOCAL ivfflat.iterative_scan = relaxed_order",
            f"SET LOCAL ivfflat.max_probes = {int(search_config['ivfflat_max_probes'])}",
            "SET LOCAL work_mem = '64MB'",
            "SET LOCAL enable_seqscan = off",
        )
    for setting in settings:
        # A savepoint per setting: a failed statement aborts the whole
        # transaction in Postgres, so without it an older pgvector rejecting
        # one setting would take the search down with it.
        try:
            with session.begin_nested():
                session.execute(text(setting))
        except Exception as e:
            logger.debug("Could not apply %r (older pgvector?): %s", setting, e)

    query_params: Dict[str, Any] = {
        "query_embedding": query_vec_str,
        "initial_limit": initial_limit,
        "max_chunks_per_doc": max_chunks_per_doc,
        "max_results": max_results,
        **filter_params_dict,
    }

    # Optional EXPLAIN ANALYZE
    if explain_analyse:
        explain_query = f"EXPLAIN (ANALYSE, BUFFERS) {vector_query}"
        explain_results = session.execute(text(explain_query), query_params)
        explain_output = [
            str(row[0] if isinstance(row, tuple) else row) for row in explain_results
        ]
        logger.info("Query execution plan:\n" + "\n".join(explain_output))
        print("Query execution plan:\n" + "\n".join(explain_output))

    results = session.execute(text(vector_query), query_params).all()
    # SET LOCAL outlives this query when the caller's savepoint is released:
    # the full-text half of a hybrid search runs in the same transaction.
    if timeout_ms > 0:
        session.execute(text("SET LOCAL statement_timeout = DEFAULT"))

    logger.debug(
        "Using pgvector native operators with CTEs (%s path), retrieved %d chunk results",
        vector_path, len(results),
    )

    if not results:
        return _empty(vector_path=vector_path, filtered_chunks=filtered_chunks)

    # Group by document and fetch Document objects
    dedup_start = time.time()

    # Group chunks by document
    doc_chunks: Dict[str, List[Dict[str, Any]]] = {}
    for row in results:
        doc_id = row.document_id
        if doc_id not in doc_chunks:
            doc_chunks[doc_id] = []

        chunk_info = {
            "chunk_id": row.chunk_id,
            "chunk_index": row.chunk_index,
            "chunk_strategy": row.chunk_strategy,
            "similarity": float(row.score),
            "char_start": row.char_start_index,
            "char_end": row.char_end_index,
            "token_length": row.token_length,
            "section_path": row.section_path if row.section_path else None,
        }
        doc_chunks[doc_id].append(chunk_info)

    # Fetch Document objects only for documents in results
    unique_doc_ids = list(doc_chunks.keys())
    documents_by_id = {
        doc.id: doc
        for doc in session.query(Document).filter(Document.id.in_(unique_doc_ids)).all()
    }

     # Build final results
    final_results = []
    for doc_id, chunks in doc_chunks.items():
        doc = documents_by_id.get(doc_id)
        if not doc:
            continue

        # Rebuild each chunk's text from the live document and sort best-first
        # (also collapses a split summary back to one result).
        from .chunk_text import attach_chunk_text
        chunks = attach_chunk_text(chunks, doc, score_key="similarity")

        from .provenance import doc_provenance
        result_dict = {
            "doc_uid": doc.id,
            "doc_id": doc.doc_id,
            "doc_source_id": doc.source_id,
            "doc_uri": doc.uri,
            "doc_title": doc.title if doc.title else doc.title_gen if doc.title_gen else None,
            "best_similarity": chunks[0]["similarity"],
            "chunks": chunks,
            "document": doc,
        }
        result_dict.update(doc_provenance(doc))
        final_results.append(result_dict)

    # Sort documents by best chunk similarity
    final_results.sort(
        key=lambda x: x["best_similarity"] if x["best_similarity"] else 0,
        reverse=True
    )

    time_deduplication = time.time() - dedup_start

    time_search_total = time.time() - start_time
    return {
        "results": final_results,
        "metadata": {
            "time_search_total": time_search_total,
            "time_embedding": embedding_time,
            "time_deduplication": time_deduplication,
            "total_results": len(final_results),
            "embedding_name": embedding_name,
            "max_results": max_results,
            "vector_path": vector_path,
            "filtered_chunks": filtered_chunks,
        },
    }

