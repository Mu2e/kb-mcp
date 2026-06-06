"""Hybrid search combining semantic (vector) and full-text search using RRF."""

import logging
import time
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)



def search_hybrid(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    parser_id: Optional[str] = None,
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
            - fulltext_rank: Position/rank in full-text search results list (1st, 2nd, 3rd, etc., None if not in results)
            - fulltext_score: Relevance score from full-text search (in chunks, if from fulltext)
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
                print(f"  Fulltext rank (position): {result.get('fulltext_rank', 'N/A')}")
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
                parser_id=parser_id,
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
                parser_id=parser_id,
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

        def get_rank_map(result_set, score_key):
            # Flatten to sort globally
            all_chunks = []
            for doc in result_set.get("results", []):
                for chunk in doc["chunks"]:
                    all_chunks.append(chunk)
            
            # Sort by their original score (descending)
            all_chunks.sort(key=lambda x: x[score_key], reverse=True)
            
            # Map ID -> Rank (1-based)
            return {chunk["chunk_id"]: i + 1 for i, chunk in enumerate(all_chunks)}

        rank_map_semantic = get_rank_map(semantic_results, "similarity")
        rank_map_fulltext = get_rank_map(fulltext_results, "score")

        fulltext_index = {} # so we only need to loop ones
        for doc in fulltext_results.get("results", []):
            for chunk in doc["chunks"]:
                fulltext_index[chunk["chunk_id"]] = {"chunk": chunk, "doc": doc}

        final_docs_map = {}
        # loop over semantic results and merge in the fulltext chunks
        for doc in semantic_results.get("results", []):
            doc_id = doc["doc_id"]
            final_docs_map[doc_id] = doc

            for chunk in doc["chunks"]:
                c_id = chunk["chunk_id"]
                rank_semantic = rank_map_semantic[c_id]
                rrf_score = 1.0 / (rrf_k + rank_semantic)

                if "rrf_rank" not in chunk:
                    chunk["rrf_rank"] = {"semantic":rank_semantic}
                else:
                    chunk["rrf_rank"]["semantic"] = rank_semantic

                if c_id in fulltext_index:
                    rank_fulltext = rank_map_fulltext[c_id]
                    rrf_score += 1.0 / (rrf_k + rank_fulltext)

                    chunk["rrf_rank"]["fulltext"] = rank_fulltext

                    # merge chunk metadata
                    #chunk["retrived"] = "semantic,fulltext"
                    chunk["score"] = fulltext_index[c_id]["chunk"]["score"]
                
                    # lets make sure we have the best score for the doc
                    if "best_score" not in final_docs_map[doc_id]:
                        final_docs_map[doc_id]["best_score"] = chunk["score"]
                    else:
                        final_docs_map[doc_id]["best_score"] = max(final_docs_map[doc_id]["best_score"], chunk["score"])

                    del fulltext_index[c_id]
                #else:
                #    chunk["retrived"] = "semantic"

                chunk["rrf_score"] = rrf_score

        # merge all remaining fulltext chunks
        for c_id, data in fulltext_index.items():
            chunk = data["chunk"]
            source_doc = data["doc"]
            doc_id = source_doc["doc_id"]

            rank_fulltext = rank_map_fulltext[c_id]
            chunk["rrf_score"] = 1.0 / (rrf_k + rank_fulltext)
            if "rrf_rank" not in chunk:
                chunk["rrf_rank"] = {"fulltext":rank_fulltext}
            else:
                chunk["rrf_rank"]["fulltext"] = rank_fulltext
            #chunk["retrived"] = "fulltext"

            if doc_id in final_docs_map:
                final_docs_map[doc_id]["chunks"].append(chunk)
                final_docs_map[doc_id]["best_score"] = source_doc["best_score"]
            else:
                new_doc = source_doc.copy()
                new_doc["chunks"] = [chunk]
                final_docs_map[doc_id] = new_doc
                
        final_results = list(final_docs_map.values())

        # sort based on rrf_score
        for doc in final_results:
            doc["chunks"].sort(key=lambda x: x["rrf_score"], reverse=True)
            doc["best_rrf"] = doc["chunks"][0]["rrf_score"]

        final_results.sort(key=lambda x: x["best_rrf"], reverse=True)
        final_results = final_results[:max_results]

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
                "semantic_results": semantic_results["metadata"]["total_results"],
                "fulltext_results": fulltext_results["metadata"]["total_results"],
                "hybrid_results": len(final_results),
                "rrf_k": rrf_k,
            },
        }
