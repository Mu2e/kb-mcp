"""Full-text search implementation using PostgreSQL tsvector."""

import logging
import time
from typing import List, Optional, Dict, Any

from sqlalchemy import text

logger = logging.getLogger(__name__)


def search_fulltext(
    query: str,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    parser_id: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    explain_analyse: bool = False,
    max_chunks_per_doc: Optional[int] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Search for documents using PostgreSQL full-text search.

    Uses the text_search_vector column on chunks table which combines:
    - document.title or document.title_gen (weight 'A' - highest)
    - chunk.text (weight 'B')
    - document.summary (weight 'D' - lower)

    Args:
        query: Search query text (will be converted to tsquery)
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy (e.g., "tokens", "slide").
                         If None, searches across all chunking strategies.
        filter: Optional Elasticsearch-style filter query (dict). Supports same filters
                as semantic search (term, terms, range, match, wildcard, bool).
        session: Optional database session
        explain_analyse: If True, print EXPLAIN ANALYZE output for the query (PostgreSQL only)
        **kwargs: Simple metadata filters (backward compatible). Direct field names are treated
                 as metadata filters. Example: author="John" filters meta.author == "John"

    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks with their relevance scores (sorted by score, best first)
        - metadata: Dictionary with search metadata:
            - time_search_total: Total time taken to execute the search (in seconds)
            - time_deduplication: Time taken to deduplicate and group results by document (in seconds)
            - total_results: Number of documents returned
            - query: The original search query
            - max_results: Maximum results requested

    Example:
        ```python
        from kb_mcp.kb.search.search_fulltext import search_fulltext

        # Basic search
        response = search_fulltext("machine learning algorithms")

        # With filters
        response = search_fulltext(
            "neural networks",
            source_id="arxiv",
            filter={"range": {"date": {"gte": "2020-01-01"}}}
        )

        # Access results
        for result in response['results']:
            print(f"Document: {result['document'].title}")
            if result['chunks']:
                print(f"  Best match score: {result['chunks'][0]['score']:.3f}")
        ```
    """
    from ..database import get_db_session

    # Determine if we own the session
    should_close = session is None

    with get_db_session(session) as session:
        # Track total time from the beginning
        start_time = time.time()

        # Check if we're using PostgreSQL
        dialect_name = session.bind.dialect.name if session.bind else None

        if dialect_name != "postgresql":
            raise NotImplementedError(
                "Full-text search is only supported on PostgreSQL. "
                "Current database dialect: " + str(dialect_name)
            )

        # Build WHERE clause using unified helper
        from .filters import build_where_clause
        where_clause_sql, filter_params_dict = build_where_clause(
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            parser_id=parser_id,
            filter=filter,
            skip_kwargs={"session", "explain_analyse", "max_results", "embedding_name"},
            **kwargs
        )

        # Get search configuration
        from ...config import get_search_config
        search_config = get_search_config()

        # Get more chunks initially to account for deduplication
        initial_limit = max_results * search_config['initial_limit_multiplier']

        # Limit chunks per document for diversity (allow override via parameter)
        if max_chunks_per_doc is None:
            max_chunks_per_doc = search_config['max_chunks_per_doc']

        # Build the full-text search query
        # Use websearch_to_tsquery for natural language queries
        # Use ts_rank_cd (cover density) which rewards proximity of matching terms
        # Deduplicate to top documents in SQL using window functions
        fulltext_query = f"""
            WITH query AS (
                SELECT websearch_to_tsquery('english', :query) AS tsq
            ),
            base_candidates AS (
                SELECT
                    c.id AS chunk_id,
                    c.document_id,
                    c.chunk_index,
                    c.chunk_strategy,
                    c.char_start_index,
                    c.char_end_index,
                    c.token_length,
                    c.section_path,
                    ts_rank_cd(c.text_search_vector, query.tsq) AS score,
                    ROW_NUMBER() OVER (PARTITION BY c.document_id ORDER BY ts_rank_cd(c.text_search_vector, query.tsq) DESC) AS rank_in_doc
                FROM chunks c
                CROSS JOIN query
                JOIN documents d ON c.document_id = d.id
                WHERE c.text_search_vector @@ query.tsq
                  AND {where_clause_sql}
                ORDER BY score DESC
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
                ORDER BY MAX(score) DESC
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

        query_params: Dict[str, Any] = {
            "query": query,
            "initial_limit": initial_limit,
            "max_chunks_per_doc": max_chunks_per_doc,
            "max_results": max_results,
            **filter_params_dict,
        }

        # Optional EXPLAIN ANALYZE
        if explain_analyse:
            explain_query = f"EXPLAIN (ANALYSE, BUFFERS) {fulltext_query}"
            explain_results = session.execute(text(explain_query), query_params)
            explain_output = [
                str(row[0] if isinstance(row, tuple) else row) for row in explain_results
            ]
            logger.info("Full-text search query execution plan:\n" + "\n".join(explain_output))

        results = session.execute(text(fulltext_query), query_params).all()

        logger.debug(
            "Full-text search retrieved %d chunk results",
            len(results),
        )

        if not results:
            time_search_total = time.time() - start_time
            return {
                "results": [],
                "metadata": {
                    "time_search_total": time_search_total,
                    "total_results": 0,
                    "query": query,
                    "max_results": max_results,
                },
            }

        # Group by document and fetch Document objects
        from .search import log_search
        from ..db_models import Document

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
                "score": float(row.score),
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

            # Sort chunks by score (best first)
            chunks.sort(key=lambda x: x["score"], reverse=True)

            final_results.append({
                "doc_uid": doc.id,
                "doc_id": doc.doc_id,
                "doc_source_id": doc.source_id,
                "doc_uri": doc.uri,
                "doc_title": doc.title if doc.title else doc.title_gen if doc.title_gen else None,
                "best_score": chunks[0]["score"],  # Using score as similarity for fulltext
                "chunks": chunks,
                "document": doc,
            })

        # Sort documents by best chunk similarity (score for fulltext)
        final_results.sort(
            key=lambda x: x["best_score"] if x["best_score"] else 0,
            reverse=True
        )

        time_deduplication = time.time() - dedup_start

        time_search_total = time.time() - start_time

        # Log search to database
        log_search(
            search_type="fulltext",
            query=query,
            final_results=final_results,
            session=session,
            should_close=should_close,
            max_results=max_results,
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            filter=filter,
            time_search_total=time_search_total,
            time_deduplication=time_deduplication,
            **kwargs
        )

        return {
            "results": final_results,
            "metadata": {
                "time_search_total": time_search_total,
                "time_deduplication": time_deduplication,
                "total_results": len(final_results),
                "query": query,
                "max_results": max_results,
            },
        }
