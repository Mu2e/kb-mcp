"""SQLite fallback search implementation."""

import logging
import time
from typing import List, Optional, Dict, Any
import numpy as np
from sqlalchemy import select, and_
from sqlalchemy.orm import aliased

from ..db_models import Document
from ..embedding.db_models import Chunk
from .filters import get_filters_fallback

logger = logging.getLogger(__name__)


def _search_fallback(
    session,
    embedding_table,
    query_embedding: List[float],
    max_results: int,
    source_id: Optional[str],
    doc_type: Optional[str],
    chunking_strategy: Optional[str],
    filter: Optional[Dict[str, Any]],
    embedding_time: float = 0.0,
    start_time: Optional[float] = None,
    max_chunks_per_doc: Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Fallback search implementation for SQLite or environments without pgvector.

    Uses SQLAlchemy ORM to fetch embeddings + chunk/document metadata with filters
    applied, then calculates cosine similarity in Python (vectorized with numpy),
    groups by document, and returns the standard search response structure.
    """
    # Derive embedding_name from table name (strip "embeddings_" prefix)
    embedding_name = embedding_table.name.replace("embeddings_", "", 1) if embedding_table.name.startswith("embeddings_") else embedding_table.name
    
    # Start timing if not provided
    if start_time is None:
        start_time = time.time()
    
    # Get search configuration for max_chunks_per_doc
    if max_chunks_per_doc is None:
        from ...config import get_search_config
        search_config = get_search_config()
        max_chunks_per_doc = search_config['max_chunks_per_doc']
    
    # Build base query: embeddings -> chunks -> documents
    chunk_alias = aliased(Chunk)
    doc_alias = aliased(Document)

    query_stmt = (
        select(
            embedding_table.c.id.label("embedding_id"),
            embedding_table.c.chunk_id,
            embedding_table.c.embedding,
            chunk_alias.id.label("chunk_id_full"),
            chunk_alias.document_id,
            chunk_alias.chunk_index,
            chunk_alias.chunk_strategy,
            chunk_alias.char_start_index,
            chunk_alias.char_end_index,
            chunk_alias.token_length,
            chunk_alias.section_path,
            doc_alias.id.label("doc_id"),
            doc_alias.source_id,
            doc_alias.doc_type,
            doc_alias.doc_id.label("doc_doc_id"),
            doc_alias.meta,
        )
        .join(chunk_alias, embedding_table.c.chunk_id == chunk_alias.id)
        .join(doc_alias, chunk_alias.document_id == doc_alias.id)
    )

    # Get dialect name for filter building
    dialect_name = session.bind.dialect.name if session.bind else None
    
    # Apply filters via helper
    filters = get_filters_fallback(
        doc_alias,
        source_id=source_id,
        doc_type=doc_type,
        filter=filter,
        dialect_name=dialect_name,
        **kwargs,
    )
    if filters:
        query_stmt = query_stmt.where(and_(*filters))
    
    # Apply chunking_strategy filter
    if chunking_strategy:
        query_stmt = query_stmt.where(chunk_alias.chunk_strategy == chunking_strategy)

    # Fetch all filtered results
    fetch_start = time.time()
    results = session.execute(query_stmt).all()
    fetch_time = time.time() - fetch_start
    logger.debug(
        "SQLite search: fetched %d embeddings in %.3fs", len(results), fetch_time
    )

    if not results:
        time_search_total = time.time() - start_time
        return {
            "results": [],
            "metadata": {
                "time_search_total": time_search_total,
                "time_embedding": embedding_time,
                "time_db_fetch": fetch_time,
                "total_results": 0,
                "embedding_name": embedding_name,
                "max_results": max_results,
            },
        }

    # Prepare query vector once
    query_vec = np.array(query_embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)

    # Initialize timings for metadata
    convert_time = 0.0
    calc_time = 0.0

    if query_norm == 0:
        similarities = np.zeros(len(results), dtype=np.float32)
    else:
        # Convert JSON-stored embeddings (lists) directly to numpy array
        convert_start = time.time()
        embedding_vectors = np.array(
            [row.embedding for row in results], dtype=np.float32
        )
        convert_time = time.time() - convert_start
        logger.debug(
            "SQLite search: converted %d embeddings in %.3fs",
            len(results),
            convert_time,
        )

        # Batch cosine similarity
        calc_start = time.time()
        dot_products = np.dot(embedding_vectors, query_vec)
        embedding_norms = np.linalg.norm(embedding_vectors, axis=1)
        embedding_norms = np.where(embedding_norms == 0, 1.0, embedding_norms)
        similarities = dot_products / (query_norm * embedding_norms)
        calc_time = time.time() - calc_start
        logger.debug("SQLite search: calculated similarities in %.3fs", calc_time)

    # Build scored_results
    scored_results: List[Dict[str, Any]] = []
    for i, row in enumerate(results):
        scored_results.append(
            {
                "similarity": float(similarities[i]),
                "embedding_id": row.embedding_id,
                "chunk_id": row.chunk_id,
                "document_id": row.doc_id,
                "chunk_index": row.chunk_index,
                "chunk_strategy": row.chunk_strategy,
                "char_start": row.char_start_index,
                "char_end": row.char_end_index,
                "token_length": row.token_length,
                "section_path": row.section_path if row.section_path else None,
                "source_id": row.source_id,
                "doc_type": row.doc_type,
            }
        )

    # Sort by similarity (descending)
    sort_start = time.time()
    if len(scored_results) > 1000:
        similarity_array = np.array([r["similarity"] for r in scored_results])
        sorted_indices = np.argsort(similarity_array)[::-1]
        scored_results = [scored_results[i] for i in sorted_indices]
    else:
        scored_results.sort(key=lambda x: x["similarity"], reverse=True)
    time_sort_results = time.time() - sort_start

    # Optionally limit scored_results before grouping for very large datasets
    if len(scored_results) > max_results * 20:
        initial_limit = max_results * 10
        scored_results = scored_results[:initial_limit]

    # Group by document and keep best chunks per document
    dedup_start = time.time()
    unique_doc_ids = list({r["document_id"] for r in scored_results})
    documents_by_id = {
        doc.id: doc
        for doc in session.query(Document).filter(Document.id.in_(unique_doc_ids)).all()
    }

    # Group chunks by document
    doc_chunks: Dict[str, List[Dict[str, Any]]] = {}
    for result in scored_results:
        doc_id = result["document_id"]
        if doc_id not in doc_chunks:
            doc_chunks[doc_id] = []

        chunk_info = {
            "chunk_id": result["chunk_id"],
            "chunk_index": result["chunk_index"],
            "chunk_strategy": result.get("chunk_strategy"),
            "similarity": result["similarity"],
            "char_start": result["char_start"],
            "char_end": result["char_end"],
            "token_length": result["token_length"],
            "section_path": result.get("section_path"),
        }
        doc_chunks[doc_id].append(chunk_info)

    # Sort chunks by similarity within each document and limit to max_chunks_per_doc
    for doc_id in doc_chunks:
        doc_chunks[doc_id].sort(key=lambda x: x["similarity"], reverse=True)
        doc_chunks[doc_id] = doc_chunks[doc_id][:max_chunks_per_doc]

    # Build final results with same structure as search_pgvector
    final_results = []
    for doc_id, chunks in doc_chunks.items():
        doc = documents_by_id.get(doc_id)
        if not doc or not chunks:
            continue

        # Add text to chunks
        for chunk in chunks:
            if chunk["char_start"] is not None and \
               chunk["char_end"] is not None and \
               documents_by_id[doc_id].text is not None:
                chunk["text"] = documents_by_id[doc_id].text[chunk["char_start"]:chunk["char_end"]]
            if chunk["chunk_strategy"] == "summary" and documents_by_id[doc_id].summary is not None:
                chunk["text"] = documents_by_id[doc_id].summary

        # Sort chunks by similarity (best first)
        chunks.sort(key=lambda x: x["similarity"], reverse=True)

        final_results.append({
            "doc_uid": doc.id,
            "doc_id": doc.doc_id,
            "doc_source_id": doc.source_id,
            "doc_uri": doc.uri,
            "doc_title": doc.title if doc.title else doc.title_gen if doc.title_gen else None,
            "best_similarity": chunks[0]["similarity"],
            "chunks": chunks,
            "document": doc,
        })

    time_deduplication = time.time() - dedup_start

    # Sort documents by best chunk similarity
    final_results.sort(
        key=lambda x: x["best_similarity"] if x["best_similarity"] else 0,
        reverse=True
    )

    # Limit to max_results documents
    final_results = final_results[:max_results]

    time_sort = time_sort_results

    time_search_total = time.time() - start_time
    return {
        "results": final_results,
        "metadata": {
            "time_search_total": time_search_total,
            "time_embedding": embedding_time,
            "time_db_fetch": fetch_time,
            "time_distance_calc": convert_time + calc_time,
            "time_sort": time_sort,
            "time_deduplication": time_deduplication,
            "total_results": len(final_results),
            "embedding_name": embedding_name,
            "max_results": max_results,
        },
    }

