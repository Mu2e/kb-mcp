"""Hybrid search combining semantic (vector) and full-text search using RRF."""

import logging
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    results_lists: List[List[Dict[str, Any]]],
    k: int = 60
) -> List[Dict[str, Any]]:
    """
    Combine multiple ranked lists using Reciprocal Rank Fusion (RRF).

    RRF formula: RRF(d) = Σ 1 / (k + rank(d))
    where k is a constant (default 60) and rank(d) is the rank of document d in each list.

    This is position-based ranking that works well when combining results from
    different retrieval methods with incomparable scores (like cosine similarity
    and ts_rank).

    Args:
        results_lists: List of result lists, where each result list contains
                      dictionaries with at least a "document_id" key
        k: Constant for RRF formula (default 60, from original paper)

    Returns:
        Combined and re-ranked list of results, sorted by RRF score (best first).
        Each result dict will have an added "rrf_score" field.

    Reference:
        Cormack, G. V., Clarke, C. L., & Buettcher, S. (2009).
        "Reciprocal rank fusion outperforms condorcet and individual rank learning methods"
    """
    # Track RRF scores for each document
    doc_scores: Dict[str, float] = {}
    # Track original result info for each document (from first list that contains it)
    doc_info: Dict[str, Dict[str, Any]] = {}

    # Process each results list
    for results_list in results_lists:
        for rank, result in enumerate(results_list, start=1):
            doc_id = result["document_id"]

            # Calculate RRF contribution: 1 / (k + rank)
            rrf_contribution = 1.0 / (k + rank)

            # Add to cumulative score
            if doc_id in doc_scores:
                doc_scores[doc_id] += rrf_contribution
            else:
                doc_scores[doc_id] = rrf_contribution
                # Store the first occurrence's info
                doc_info[doc_id] = result.copy()

    # Build final results list with RRF scores
    combined_results = []
    for doc_id, rrf_score in doc_scores.items():
        result = doc_info[doc_id].copy()
        result["rrf_score"] = rrf_score
        combined_results.append(result)

    # Sort by RRF score (descending)
    combined_results.sort(key=lambda x: x["rrf_score"], reverse=True)

    return combined_results


def search_hybrid(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    semantic_weight: float = 0.5,
    fulltext_weight: float = 0.5,
    rrf_k: int = 60,
    explain_analyse: bool = False,
    max_chunks_per_doc: Optional[int] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Hybrid search combining semantic (vector) and full-text search.

    Uses Reciprocal Rank Fusion (RRF) to combine results from both search methods.
    This provides the benefits of both approaches:
    - Semantic search: finds conceptually similar content
    - Full-text search: finds exact keyword matches

    Args:
        query: Search query text
        embedding_name: Name of the embedding to use for semantic search (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy (e.g., "tokens", "slide").
                         If None, searches across all chunking strategies.
        filter: Optional Elasticsearch-style filter query (dict). Supports same filters
                as semantic search (term, terms, range, match, wildcard, bool).
        session: Optional database session
        semantic_weight: Weight for semantic search results (0-1, default 0.5).
                        Currently unused - RRF is position-based, not score-based.
                        Kept for future weighted implementations.
        fulltext_weight: Weight for full-text search results (0-1, default 0.5).
                        Currently unused - RRF is position-based, not score-based.
                        Kept for future weighted implementations.
        rrf_k: Constant for RRF formula (default 60). Higher values give less weight
               to top-ranked items, making the fusion more conservative.
        explain_analyse: If True, print EXPLAIN ANALYZE output for both queries
        **kwargs: Simple metadata filters (backward compatible)

    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks (merged from both search methods)
            - rrf_score: Reciprocal Rank Fusion score
            - semantic_rank: Rank from semantic search (None if not in results)
            - fulltext_rank: Rank from full-text search (None if not in results)
        - metadata: Dictionary with search metadata:
            - time_search_total: Total time for hybrid search
            - time_semantic: Time for semantic search
            - time_fulltext: Time for full-text search
            - time_fusion: Time for RRF fusion
            - total_results: Number of documents returned
            - query: The original search query
            - max_results: Maximum results requested
            - semantic_results: Number of results from semantic search
            - fulltext_results: Number of results from full-text search

    Example:
        ```python
        from kb_mcp.kb.search.search_hybrid import search_hybrid

        # Basic hybrid search
        response = search_hybrid("neural network architectures")

        # With custom RRF parameter
        response = search_hybrid(
            "machine learning",
            rrf_k=100,  # More conservative fusion
            max_results=20
        )

        # Access results
        for result in response['results']:
            print(f"Document: {result['document'].title}")
            print(f"  RRF Score: {result['rrf_score']:.4f}")
            print(f"  Semantic rank: {result.get('semantic_rank', 'N/A')}")
            print(f"  Fulltext rank: {result.get('fulltext_rank', 'N/A')}")
        ```
    """
    from ..database import get_db_session
    from .search import search_semantic
    from .search_fulltext import search_fulltext
    from ..db_models import Document

    # Determine if we own the session
    should_close = session is None

    with get_db_session(session) as session:
        # Track total time
        total_start = time.time()

        # Run semantic search
        semantic_start = time.time()
        try:
            semantic_results = search_semantic(
                query=query,
                embedding_name=embedding_name,
                max_results=max_results * 2,  # Get more to improve fusion
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter,
                session=session,
                explain_analyse=explain_analyse,
                max_chunks_per_doc=max_chunks_per_doc,
                **kwargs
            )
        except Exception as e:
            logger.warning(f"Semantic search failed: {e}")
            semantic_results = {"results": [], "metadata": {}}

        semantic_time = time.time() - semantic_start

        # Run full-text search
        fulltext_start = time.time()
        try:
            fulltext_results = search_fulltext(
                query=query,
                max_results=max_results * 2,  # Get more to improve fusion
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter,
                session=session,
                explain_analyse=explain_analyse,
                max_chunks_per_doc=max_chunks_per_doc,
                **kwargs
            )
        except Exception as e:
            logger.warning(f"Full-text search failed: {e}")
            fulltext_results = {"results": [], "metadata": {}}

        fulltext_time = time.time() - fulltext_start

        # Prepare results lists for RRF
        fusion_start = time.time()

        # Convert semantic results to simple list with document_id
        semantic_list = [
            {
                "document_id": result["document"].id,
                "document": result["document"],
                "chunks": result["chunks"],
                "method": "semantic",
            }
            for result in semantic_results.get("results", [])
        ]

        # Convert full-text results to simple list with document_id
        fulltext_list = [
            {
                "document_id": result["document"].id,
                "document": result["document"],
                "chunks": result["chunks"],
                "method": "fulltext",
            }
            for result in fulltext_results.get("results", [])
        ]

        # Apply RRF to combine rankings
        combined_results = reciprocal_rank_fusion(
            [semantic_list, fulltext_list],
            k=rrf_k
        )

        # Track which method contributed each result
        semantic_ranks = {
            result["document_id"]: rank + 1
            for rank, result in enumerate(semantic_list)
        }
        fulltext_ranks = {
            result["document_id"]: rank + 1
            for rank, result in enumerate(fulltext_list)
        }

        # Build final results with merged chunk info
        final_results = []
        for result in combined_results[:max_results]:
            doc_id = result["document_id"]

            # Collect chunks from both methods
            all_chunks = []
            chunks_seen = set()

            # Add semantic chunks
            if doc_id in semantic_ranks:
                for sem_result in semantic_list:
                    if sem_result["document_id"] == doc_id:
                        for chunk in sem_result["chunks"]:
                            chunk_id = chunk["chunk_id"]
                            if chunk_id not in chunks_seen:
                                chunk_copy = chunk.copy()
                                chunk_copy["from_semantic"] = True
                                chunk_copy["from_fulltext"] = False
                                all_chunks.append(chunk_copy)
                                chunks_seen.add(chunk_id)
                        break

            # Add or merge full-text chunks
            if doc_id in fulltext_ranks:
                for ft_result in fulltext_list:
                    if ft_result["document_id"] == doc_id:
                        for chunk in ft_result["chunks"]:
                            chunk_id = chunk["chunk_id"]
                            if chunk_id in chunks_seen:
                                # Chunk already added from semantic search - mark it as in both
                                for existing_chunk in all_chunks:
                                    if existing_chunk["chunk_id"] == chunk_id:
                                        existing_chunk["from_fulltext"] = True
                                        existing_chunk["fulltext_rank"] = chunk.get("rank")
                                        break
                            else:
                                # New chunk from full-text only
                                chunk_copy = chunk.copy()
                                chunk_copy["from_semantic"] = False
                                chunk_copy["from_fulltext"] = True
                                all_chunks.append(chunk_copy)
                                chunks_seen.add(chunk_id)
                        break

            # Sort chunks: prioritize chunks found in both methods, then by best score/rank
            def chunk_sort_key(c):
                in_both = c.get("from_semantic", False) and c.get("from_fulltext", False)
                semantic_sim = c.get("similarity", 0) if c.get("from_semantic") else 0
                fulltext_rank = c.get("rank", 0) if c.get("from_fulltext") else 0
                # Prioritize: 1) in both, 2) semantic similarity, 3) fulltext rank
                return (not in_both, -semantic_sim, -fulltext_rank)

            all_chunks.sort(key=chunk_sort_key)

            final_results.append({
                "document": result["document"],
                "chunks": all_chunks,
                "rrf_score": result["rrf_score"],
                "semantic_rank": semantic_ranks.get(doc_id),
                "fulltext_rank": fulltext_ranks.get(doc_id),
            })

        fusion_time = time.time() - fusion_start
        total_time = time.time() - total_start

        # Log search to database
        from .search import log_search

        log_search(
            search_type="hybrid",
            query=query,
            final_results=final_results,
            session=session,
            should_close=should_close,
            max_results=max_results,
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            filter=filter,
            embedding_name=embedding_name,
            time_search_total=total_time,
            time_semantic=semantic_time,
            time_fulltext=fulltext_time,
            time_fusion=fusion_time,
            **kwargs
        )

        return {
            "results": final_results,
            "metadata": {
                "time_search_total": total_time,
                "time_semantic": semantic_time,
                "time_fulltext": fulltext_time,
                "time_fusion": fusion_time,
                "total_results": len(final_results),
                "query": query,
                "max_results": max_results,
                "semantic_results": len(semantic_list),
                "fulltext_results": len(fulltext_list),
                "rrf_k": rrf_k,
            },
        }
