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

    vector_query = f"""
        WITH base_candidates AS (
            SELECT
                c.id AS chunk_id,
                c.document_id,
                c.chunk_index,
                c.chunk_strategy,
                c.char_start_index,
                c.char_end_index,
                c.token_length,
                c.section_path,
                calc.distance,
                (1 - calc.distance) AS score,
                ROW_NUMBER() OVER (PARTITION BY c.document_id ORDER BY calc.distance) AS rank_in_doc
            FROM {embedding_table.name} e
            JOIN chunks c ON e.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            CROSS JOIN LATERAL (
                SELECT (e.embedding <=> CAST(:query_embedding AS vector)) AS distance
            ) calc
            WHERE {where_clause_sql}
            ORDER BY calc.distance
            LIMIT :initial_limit
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

    # Set PostgreSQL session parameters for optimal query performance
    search_hyperparams = """
        SET enable_seqscan = OFF;
        SET plan_cache_mode = force_generic_plan;
    """
    session.execute(text(search_hyperparams))

    # Try to set IVFFlat probes (safe to ignore failures)
    try:
        session.execute(text("SET ivfflat.probes = 32"))
    except Exception as e:
        logger.debug(
            "Could not set ivfflat.probes (this is OK if not using IVFFlat index): %s",
            e,
        )

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

    logger.debug(
        "Using pgvector native operators with CTEs, retrieved %d chunk results",
        len(results),
    )

    if not results:
        time_search_total = time.time() - start_time
        return {
            "results": [],
            "metadata": {
                "time_search_total": time_search_total,
                "time_embedding": embedding_time,
                "total_results": 0,
                "embedding_name": embedding_name,
                "max_results": max_results,
            },
        }

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

        # add text to chunks
        for chunk in chunks:
            if chunk["char_start"] is not None and \
               chunk["char_end"] is not None and \
               documents_by_id[doc_id].text is not None:
                chunk["text"] = documents_by_id[doc_id].text[chunk["char_start"]:chunk["char_end"]]
            if chunk["chunk_strategy"] == "summary" and documents_by_id[doc_id].summary is not None:
                chunk["text"] = documents_by_id[doc_id].summary

        # Sort chunks by similarity (best first)
        chunks.sort(key=lambda x: x["similarity"], reverse=True)

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
        },
    }

