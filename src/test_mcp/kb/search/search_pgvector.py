"""PostgreSQL/pgvector search implementation."""

import logging
import time
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import text, alias

from sqlalchemy.orm import aliased

from ..db_models import Document
from .filters import get_filters_pgvector

logger = logging.getLogger(__name__)


def _search_pgvector(
    session,
    embedding_table,
    query_embedding: List[float],
    max_results: int,
    source_id: Optional[str],
    doc_type: Optional[str],
    chunking_strategy: Optional[str],
    filter: Optional[Dict[str, Any]],
    explain_analyse: bool,
    embedding_time: float = 0.0,
    start_time: Optional[float] = None,
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

    # Build WHERE clause from filter parameters
    where_parts: List[str] = []
    filter_params_dict: Dict[str, Any] = {}

    # Handle source_id filter
    if source_id:
        where_parts.append("d.source_id = :source_id")
        filter_params_dict["source_id"] = source_id

    # Handle doc_type filter
    if doc_type:
        where_parts.append("d.doc_type = :doc_type")
        filter_params_dict["doc_type"] = doc_type

    # Handle chunking_strategy filter
    if chunking_strategy:
        where_parts.append("c.chunk_strategy = :chunking_strategy")
        filter_params_dict["chunking_strategy"] = chunking_strategy

    # Handle Elasticsearch-style filter using unified parser
    if filter:
        # Create doc_alias for filter parser (matches 'd' in SQL query)
        doc_alias = aliased(Document, name="d")
        filter_sql, filter_params = get_filters_pgvector(doc_alias, filter)
        if filter_sql:
            where_parts.append(filter_sql)
            filter_params_dict.update(filter_params)
    
    # Handle simple kwargs (backward compatibility)
    for key, value in kwargs.items():
        # Skip reserved parameters
        if key in ("session", "explain_analyse", "embedding_name", "max_results"):
            continue
        
        # Treat as metadata filter (term query)
        param_name = f"meta_{key}"
        where_parts.append(f"d.meta->>'{key}' = :{param_name}")
        filter_params_dict[param_name] = str(value)

    where_clause_sql = " AND ".join(where_parts) if where_parts else "TRUE"

    # Get more chunks initially to account for deduplication
    initial_limit = max_results * 10

    vector_query = f"""
        WITH similarity_scores AS (
            SELECT
                c.id AS chunk_id,
                c.document_id AS parent_id,
                c.chunk_index,
                c.chunk_strategy,
                c.char_start_index,
                c.char_end_index,
                c.token_length,
                (1 - (e.embedding <=> CAST(:query_embedding AS vector))) AS score
            FROM {embedding_table.name} e
            JOIN chunks c ON e.chunk_id = c.id
            JOIN documents d ON c.document_id = d.id
            WHERE {where_clause_sql}
            ORDER BY e.embedding <=> CAST(:query_embedding AS vector)
            LIMIT :initial_limit
        )
        SELECT 
            ss.chunk_id,
            ss.parent_id,
            ss.chunk_index,
            ss.chunk_strategy,
            ss.char_start_index,
            ss.char_end_index,
            ss.token_length,
            ss.score,
            d.id AS doc_id,
            d.source_id,
            d.doc_type,
            d.meta
        FROM similarity_scores ss
        JOIN documents d ON ss.parent_id = d.id
        ORDER BY ss.score DESC
        LIMIT :initial_limit
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

    results = session.execute(text(vector_query), query_params).all()

    # Build scored results
    scored_results: List[Dict[str, Any]] = []
    for row in results:
        scored_results.append(
            {
                "similarity": float(row.score),
                "embedding_id": None,  # Not needed for final results
                "chunk_id": row.chunk_id,
                "document_id": row.doc_id,
                "chunk_index": row.chunk_index,
                "chunk_strategy": row.chunk_strategy,
                "char_start": row.char_start_index,
                "char_end": row.char_end_index,
                "token_length": row.token_length,
                "source_id": row.source_id,
                "doc_type": row.doc_type,
            }
        )

    logger.debug(
        "Using pgvector native operators with CTEs, retrieved %d results",
        len(scored_results),
    )

    if not scored_results:
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

    # Group by document and keep best chunks per document
    dedup_start = time.time()
    unique_doc_ids = list({r["document_id"] for r in scored_results})
    documents_by_id = {
        doc.id: doc
        for doc in session.query(Document).filter(Document.id.in_(unique_doc_ids)).all()
    }

    doc_results: Dict[str, Dict[str, Any]] = {}
    for result in scored_results:
        doc_id = result["document_id"]
        if doc_id not in doc_results:
            doc = documents_by_id.get(doc_id)
            if not doc:
                continue
            doc_results[doc_id] = {
                "document": doc,
                "chunks": [],
            }

        chunk_info = {
            "chunk_id": result["chunk_id"],
            "chunk_index": result["chunk_index"],
            "chunk_strategy": result.get("chunk_strategy"),
            "similarity": result["similarity"],
            "char_start": result["char_start"],
            "char_end": result["char_end"],
            "token_length": result["token_length"],
        }
        doc_results[doc_id]["chunks"].append(chunk_info)

    final_results = list(doc_results.values())
    time_deduplication = time.time() - dedup_start

    # Sort chunks within each document by similarity (best first)
    for result in final_results:
        result["chunks"].sort(key=lambda x: x["similarity"], reverse=True)

    # Sort documents by best similarity (first chunk's similarity)
    final_results.sort(key=lambda x: x["chunks"][0]["similarity"] if x["chunks"] else 0, reverse=True)
    
    # Limit to max_results documents
    final_results = final_results[:max_results]

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

