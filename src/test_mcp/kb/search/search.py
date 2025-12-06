"""Main search function - embedd query and get_closest."""

import logging
import time
from typing import Optional, Dict, Any

from ..database import get_db_session
from ..embedding.utils import get_embedder
from .core import SearchLog
from .similarity import get_closest

logger = logging.getLogger(__name__)


def search(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    explain_analyse: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """
    Search for documents using vector similarity.
    
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
        >>> # Basic search
        >>> response = search("track reconstruction", embedding_name="openai-small")
        
        >>> # Simple metadata filter (backward compatible)
        >>> response = search("track reconstruction", author="John Doe")
        
        >>> # Elasticsearch-style term query (exact match)
        >>> response = search("track reconstruction", filter={"term": {"author": "John Doe"}})
        
        >>> # Elasticsearch-style terms query (OR - match any)
        >>> response = search("track reconstruction", filter={"terms": {"author": ["John Doe", "Jane Smith"]}})
        
        >>> # Elasticsearch-style range query (date range)
        >>> response = search(
        ...     "track reconstruction",
        ...     filter={"range": {"date": {"gte": "2020-01-01", "lte": "2023-12-31"}}}
        ... )
        
        >>> # Elasticsearch-style match query (contains/substring)
        >>> response = search("track reconstruction", filter={"match": {"author": "Simon"}})
        
        >>> # Elasticsearch-style wildcard query (pattern matching)
        >>> response = search("track reconstruction", filter={"wildcard": {"author": "Sim*n"}})
        
        >>> # Elasticsearch-style bool query (complex filtering)
        >>> response = search(
        ...     "track reconstruction",
        ...     filter={
        ...         "bool": {
        ...             "must": [
        ...                 {"term": {"author": "John Doe"}},
        ...                 {"range": {"date": {"gte": "2020-01-01"}}}
        ...             ],
        ...             "should": [
        ...                 {"term": {"category": "A"}},
        ...                 {"term": {"category": "B"}}
        ...             ],
        ...             "minimum_should_match": 1
        ...         }
        ...     }
        ... )
        
        >>> # Combined: simple kwargs + filter
        >>> response = search(
        ...     "track reconstruction",
        ...     chunking_strategy="tokens",
        ...     source_id="atlas-docdb",
        ...     author="John Doe",  # Simple filter
        ...     filter={"range": {"date": {"gte": "2020"}}}  # Complex filter
        ... )
    
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
    
        >>> # Access results
        >>> for result in response['results']:
        ...     print(f"Document: {result['document'].id}")
        ...     if result['chunks']:
        ...         print(f"  Best chunk: {result['chunks'][0]['chunk_id']}, Score: {result['chunks'][0]['similarity']:.3f}")
        
        >>> # Check timing information
        >>> print(f"Total search time: {response['metadata']['time_search_total']:.3f}s")
        >>> print(f"Embedding time: {response['metadata']['time_embedding']:.3f}s")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()
    
    try:
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
            filter=filter,
            session=session,
            explain_analyse=explain_analyse,
            **kwargs
        )
        
        # Update metadata with query and embedding time (for logging)
        result['metadata']['query'] = query
        result['metadata']['time_embedding'] = embedding_time
        result['metadata']['time_search_total'] = time.time() - total_start
        
        # Log search to database
        # Extract document IDs while session is still active to avoid detachment issues
        try:
            # Build results list with document_id and chunk_ids for each result
            # Extract document IDs while session is still active
            results_data = []
            for doc_result in result['results']:
                doc = doc_result['document']
                # Extract ID while session is active
                doc_id = doc.id
                results_data.append({
                    "document_id": doc_id,
                    "chunk_ids": [chunk['chunk_id'] for chunk in doc_result['chunks']]
                })

            best_similarity = None
            if result['results'] and result['results'][0]['chunks']:
                best_similarity = result['results'][0]['chunks'][0]['similarity']


            search_log = SearchLog(
                query=query,
                embedding_name=embedding_name,
                max_results=max_results,
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter_params=filter,
                metadata_filters=kwargs if kwargs else None,
                results=results_data,
                best_similarity=result['results'][0]['chunks'][0]['similarity'] if result['results'] and result['results'][0]['chunks'] else None,
                total_results=result['metadata']['total_results'],
                time_search_total=result['metadata']['time_search_total'],
                time_embedding=result['metadata'].get('time_embedding'),
                time_deduplication=result['metadata'].get('time_deduplication'),
                time_db_fetch=result['metadata'].get('time_db_fetch'),
                time_distance_calc=result['metadata'].get('time_distance_calc'),
                time_sort=result['metadata'].get('time_sort'),
            )

            session.add(search_log)
            
            # Only commit if we own the session; otherwise let caller handle it
            if own_session:
                session.commit()
            
        except Exception as e:
            # Don't fail the search if logging fails
            logger.warning(f"Failed to log search to database: {e}", exc_info=True)
            if own_session:
                try:
                    session.rollback()
                except Exception:
                    pass  # Ignore rollback errors
        
        return result
        
    except Exception as e:
        logger.error(f"Error during search: {e}", exc_info=True)
        raise
    finally:
        if own_session:
            session.close()

