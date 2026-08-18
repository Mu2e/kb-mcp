"""Main search function - embedd query and get_closest."""

import logging
import time
from typing import Optional, Dict, Any, List

from ..database import get_db_session
from ..embedding.utils import get_embedder
from .db_models import SearchLog
from .similarity import get_closest

logger = logging.getLogger(__name__)


def log_search(
    search_type: str,
    query: str,
    final_results: List[Dict[str, Any]],
    session,
    should_close: bool,
    # Search parameters
    max_results: int,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    embedding_name: Optional[str] = None,
    # Timing information
    time_search_total: float = 0.0,
    time_embedding: Optional[float] = None,
    time_deduplication: Optional[float] = None,
    time_db_fetch: Optional[float] = None,
    time_distance_calc: Optional[float] = None,
    time_sort: Optional[float] = None,
    time_semantic: Optional[float] = None,
    time_fulltext: Optional[float] = None,
    time_fusion: Optional[float] = None,
    # Additional kwargs
    **kwargs
) -> None:
    """
    Log a search operation to the database.

    Args:
        search_type: Type of search ("semantic", "fulltext", "hybrid")
        query: Search query string
        final_results: List of result dicts with document and chunks
        session: Database session
        should_close: Whether this function should commit (if we own the session)
        max_results: Maximum results requested
        source_id: Source filter
        doc_type: Document type filter
        chunking_strategy: Chunking strategy filter
        filter: Complex filter dict
        embedding_name: Embedding model name (for semantic/hybrid)
        time_search_total: Total search time
        time_embedding: Embedding generation time
        time_deduplication: Deduplication time
        time_db_fetch: Database fetch time
        time_distance_calc: Distance calculation time
        time_sort: Sorting time
        time_semantic: Semantic search time (hybrid only)
        time_fulltext: Fulltext search time (hybrid only)
        time_fusion: RRF fusion time (hybrid only)
        **kwargs: Additional metadata filters
    """
    try:
        # Build results list with document_id and chunk_ids
        results_data = []
        for doc_result in final_results:
            doc = doc_result['document']
            doc_id = doc.id
            results_data.append({
                "document_id": doc_id,
                "chunk_ids": [chunk['chunk_id'] for chunk in doc_result['chunks']]
            })

        # Extract best scores based on search type
        best_similarity = None
        best_rank = None

        if final_results and final_results[0]['chunks']:
            first_chunk = final_results[0]['chunks'][0]

            if search_type == "semantic":
                best_similarity = first_chunk.get('similarity')
            elif search_type == "fulltext":
                best_rank = first_chunk.get('score')  # fulltext uses 'score' not 'rank'
            elif search_type == "hybrid":
                # For hybrid, look through all chunks to find best of each
                for chunk in final_results[0]['chunks']:
                    if chunk.get('from_semantic') and chunk.get('similarity'):
                        if best_similarity is None or chunk['similarity'] > best_similarity:
                            best_similarity = chunk['similarity']
                    if chunk.get('from_fulltext') and chunk.get('score'):  # fulltext uses 'score' not 'rank'
                        if best_rank is None or chunk['score'] > best_rank:
                            best_rank = chunk['score']

        search_log = SearchLog(
            search_type=search_type,
            query=query,
            embedding_name=embedding_name,
            max_results=max_results,
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            filter_params=filter,
            metadata_filters=kwargs if kwargs else None,
            results=results_data,
            best_similarity=best_similarity,
            best_rank=best_rank,
            total_results=len(final_results),
            time_search_total=time_search_total,
            time_embedding=time_embedding,
            time_deduplication=time_deduplication,
            time_db_fetch=time_db_fetch,
            time_distance_calc=time_distance_calc,
            time_sort=time_sort,
            time_semantic=time_semantic,
            time_fulltext=time_fulltext,
            time_fusion=time_fusion,
        )

        session.add(search_log)

        # Only commit if we own the session
        if should_close:
            session.commit()

    except Exception as e:
        # Don't fail the search if logging fails
        logger.warning(f"Failed to log {search_type} search to database: {e}", exc_info=True)
        if should_close:
            try:
                session.rollback()
            except Exception:
                pass


def search_semantic(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    parser_id: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    explain_analyse: bool = False,
    max_chunks_per_doc: Optional[int] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Search for documents using semantic vector similarity.
    
    Supports Elasticsearch-style filter queries for flexible metadata filtering.
    
    Args:
        query: Search query text
        embedding_name: Name of the embedding to use (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy (e.g., "tokens", "slide").
                         If None, searches across all chunking strategies.
        filter: Optional Elasticsearch-style filter query (dict). Supports:
                - term: {"term": {"field": "value"}} - exact match
                - terms: {"terms": {"field": ["value1", "value2"]}} - match any (OR)
                - range: {"range": {"field": {"gte": "min", "lte": "max"}}} - range query
                - match: {"match": {"field": "value"}} - contains/substring match (LIKE '%value%')
                - wildcard: {"wildcard": {"field": "pattern"}} - pattern match with * and ? wildcards
                - bool: {"bool": {"must": [...], "should": [...], "must_not": [...]}} - boolean logic
        session: Optional database session
        explain_analyse: If True, print EXPLAIN ANALYZE output for the query (PostgreSQL only)
        **kwargs: Simple metadata filters (backward compatible). Direct field names are treated
                 as metadata filters. Example: author="John" filters meta.author == "John"
    
    Examples:
        ```python
        # Basic search
        response = search("track reconstruction", embedding_name="openai-small")
        
        # Simple metadata filter (backward compatible)
        response = search("track reconstruction", author="John Doe")
        
        # Elasticsearch-style term query (exact match)
        response = search("track reconstruction", filter={"term": {"author": "John Doe"}})
        
        # Elasticsearch-style terms query (OR - match any)
        response = search("track reconstruction", filter={"terms": {"author": ["John Doe", "Jane Smith"]}})
        
        # Elasticsearch-style range query (date range)
        response = search(
            "track reconstruction",
            filter={"range": {"date": {"gte": "2020-01-01", "lte": "2023-12-31"}}}
        )
        
        # Elasticsearch-style match query (contains/substring)
        response = search("track reconstruction", filter={"match": {"author": "Simon"}})
        
        # Elasticsearch-style wildcard query (pattern matching)
        response = search("track reconstruction", filter={"wildcard": {"author": "Sim*n"}})
        
        # Elasticsearch-style bool query (complex filtering)
        response = search(
            "track reconstruction",
            filter={
                "bool": {
                    "must": [
                        {"term": {"author": "John Doe"}},
                        {"range": {"date": {"gte": "2020-01-01"}}}
                    ],
                    "should": [
                        {"term": {"category": "A"}},
                        {"term": {"category": "B"}}
                    ],
                    "minimum_should_match": 1
                }
            }
        )
        
        # Combined: simple kwargs + filter
        response = search(
            "track reconstruction",
            chunking_strategy="tokens",
            source_id="atlas-docdb",
            author="John Doe",  # Simple filter
            filter={"range": {"date": {"gte": "2020"}}}  # Complex filter
        )
        ```
    
    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks with their distances (sorted by similarity, best first)
        - metadata: Dictionary with search metadata:
            - time_search_total: Total time taken to execute the search (in seconds)
            - time_embedding: Time taken to generate the query embedding (in seconds)
            - time_deduplication: Time taken to deduplicate and group results by document (in seconds)
            - time_db_fetch: Time taken to fetch data from database (SQLite only, in seconds)
            - time_distance_calc: Time taken to calculate distances/similarities (SQLite only, in seconds)
            - time_sort: Time taken to sort results (SQLite only, in seconds)
            - total_results: Number of documents returned
            - query: The original search query
            - embedding_name: The embedding model used
            - max_results: Maximum results requested
            # Future fields can be added here (e.g., summary, filters_applied, etc.)
    
        # Access results
        for result in response['results']:
            print(f"Document: {result['document'].id}")
            if result['chunks']:
                print(f"  Best chunk: {result['chunks'][0]['chunk_id']}, Score: {result['chunks'][0]['similarity']:.3f}")
        
        # Check timing information
        print(f"Total search time: {response['metadata']['time_search_total']:.3f}s")
        print(f"Embedding time: {response['metadata']['time_embedding']:.3f}s")
        ```
    """
    # Determine if we own the session
    should_close = session is None

    with get_db_session(session) as session:
        # Track total time from the beginning
        total_start = time.time()
        
        # Time the embedding generation separately
        embedding_start = time.time()
        
        # Get embedder and embed the query
        # Don't pass kwargs to embedder - metadata filters should only go to search functions
        embedder = get_embedder(embedding_name=embedding_name, session=session)
        query_embedding = embedder([query])[0]  # Get first (and only) embedding
        from ..embedding.embedding import _convert_embedding_to_list
        query_embedding = _convert_embedding_to_list(query_embedding)
        
        embedding_time = time.time() - embedding_start
        
        # Get embedding_name if not provided
        from ..embedding.utils import get_embedding_name
        embedding_name = get_embedding_name(embedding_name, session=session, embedder=embedder)
        
        # Use get_closest with the embedded query
        result = get_closest(
            embedding=query_embedding,
            embedding_name=embedding_name,
            max_results=max_results,
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
        
        # Update metadata with query and embedding time
        result['metadata']['query'] = query
        result['metadata']['time_embedding'] = embedding_time
        result['metadata']['time_search_total'] = time.time() - total_start

        # Log search to database
        log_search(
            search_type="semantic",
            query=query,
            final_results=result['results'],
            session=session,
            should_close=should_close,
            max_results=max_results,
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            filter=filter,
            embedding_name=embedding_name,
            time_search_total=result['metadata']['time_search_total'],
            time_embedding=result['metadata'].get('time_embedding'),
            time_deduplication=result['metadata'].get('time_deduplication'),
            time_db_fetch=result['metadata'].get('time_db_fetch'),
            time_distance_calc=result['metadata'].get('time_distance_calc'),
            time_sort=result['metadata'].get('time_sort'),
            **kwargs
        )

        return result


def search(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    parser_id: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    rrf_k: Optional[int] = None,
    explain_analyse: bool = False,
    max_chunks_per_doc: Optional[int] = None,
    rerank: Optional[bool] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Hybrid search combining semantic (vector) and full-text search.

    This is the main search function that provides the best of both worlds:
    - Semantic search: finds conceptually similar content using embeddings
    - Full-text search: finds exact keyword matches using PostgreSQL tsvector

    Results are combined using Reciprocal Rank Fusion (RRF) for optimal ranking.

    Args:
        query: Search query text
        embedding_name: Name of the embedding to use for semantic search (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy (e.g., "tokens", "slide").
                         If None, searches across all chunking strategies.
        filter: Optional Elasticsearch-style filter query (dict). Supports:
                - term: {"term": {"field": "value"}} - exact match
                - terms: {"terms": {"field": ["value1", "value2"]}} - match any (OR)
                - range: {"range": {"field": {"gte": "min", "lte": "max"}}} - range query
                - match: {"match": {"field": "value"}} - contains/substring match
                - wildcard: {"wildcard": {"field": "pattern"}} - pattern match
                - bool: {"bool": {"must": [...], "should": [...], "must_not": [...]}} - boolean logic
        session: Optional database session
        rrf_k: Constant for Reciprocal Rank Fusion (default 60). Higher values make
               fusion more conservative.
        explain_analyse: If True, print EXPLAIN ANALYZE output for both queries
        **kwargs: Simple metadata filters (backward compatible)

    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks from both search methods
            - rrf_score: Reciprocal Rank Fusion score
            - semantic_rank: Rank from semantic search (None if not in results)
            - fulltext_rank: Rank from full-text search (None if not in results)
        - metadata: Dictionary with search metadata including timing info

    Examples:
        ```python
        # Basic hybrid search
        response = search("neural network architectures")

        # With filters
        response = search(
            "machine learning",
            source_id="arxiv",
            filter={"range": {"date": {"gte": "2020-01-01"}}}
        )

        # Access results
        for result in response['results']:
            print(f"Document: {result['document'].title}")
            print(f"  RRF Score: {result['rrf_score']:.4f}")
            if result['chunks']:
                chunk = result['chunks'][0]
                if chunk.get('from_semantic') and chunk.get('from_fulltext'):
                    print("  Found by both methods!")
        ```

    See Also:
        - search_semantic(): Semantic-only search using vector embeddings
        - search_fulltext(): Full-text-only search using PostgreSQL tsvector
    """
    from .search_hybrid import search_hybrid
    from ...config import get_search_config

    # Get default rrf_k from config if not provided
    if rrf_k is None:
        search_config = get_search_config()
        rrf_k = search_config['rrf_k']

    return search_hybrid(
        query=query,
        embedding_name=embedding_name,
        max_results=max_results,
        source_id=source_id,
        doc_type=doc_type,
        chunking_strategy=chunking_strategy,
        parser_id=parser_id,
        filter=filter,
        session=session,
        rrf_k=rrf_k,
        explain_analyse=explain_analyse,
        max_chunks_per_doc=max_chunks_per_doc,
        rerank=rerank,
        **kwargs
    )

