"""Core similarity search functions using embeddings."""

import logging
import time
from typing import List, Optional, Dict, Any

from ..database import get_db_session
from ..embedding.embedding import _get_embedding_table, _convert_embedding_to_list
from .search_pgvector import _search_pgvector
from .search_fallback import _search_fallback

logger = logging.getLogger(__name__)


def _empty_search_result(embedding_name: str, max_results: int, search_start: float) -> Dict[str, Any]:
    """Return empty search result structure."""
    return {
        "results": [],
        "metadata": {
            "time_search_total": time.time() - search_start,
            "time_embedding": 0.0,
            "time_deduplication": 0.0,
            "total_results": 0,
            "embedding_name": embedding_name,
            "max_results": max_results,
        }
    }


def get_closest(
    embedding: List[float],
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
    Find documents with chunks closest to the given embedding vector.
    
    This is the core similarity search function. It takes an embedding vector
    directly and finds the most similar chunks/documents.
    
    Args:
        embedding: Embedding vector (list of floats) to search for
        embedding_name: Name of the embedding model used (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy (e.g., "tokens", "slide").
                         If None, searches across all chunking strategies.
        filter: Optional Elasticsearch-style filter query. See :func:`search` for detailed
                filter documentation and examples.
        session: Optional database session
        explain_analyse: If True, print EXPLAIN ANALYZE output for the query (PostgreSQL only)
        **kwargs: Simple metadata filters. See :func:`search` for details.
    
    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks with their similarities (sorted by similarity, best first)
        - metadata: Dictionary with search metadata:
            - time_search_total: Total time taken to execute the search (in seconds)
            - total_results: Number of documents returned
            - embedding_name: The embedding model used
            - max_results: Maximum results requested
    
    Example:
        >>> from test_mcp.kb.search.similarity import get_closest
        >>> embedding = [0.1, 0.2, 0.3, ...]  # Your embedding vector
        >>> results = get_closest(
        ...     embedding,
        ...     embedding_name="openai-small",
        ...     max_results=10,
        ...     source_id="inspire-hep"
        ... )
        >>> for result in results['results']:
        ...     print(f"Document: {result['document'].id}")
        ...     print(f"  Best similarity: {result['chunks'][0]['similarity']:.3f}")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()
    
    try:
        import time
        search_start = time.time()
        
        # Convert embedding to list format if needed
        query_embedding = _convert_embedding_to_list(embedding)
        
        # Get embedding table - use default if not specified
        config, embedding_table, embedding_name = _get_embedding_table(session, embedding_name)
        
        # Check if we're using PostgreSQL with pgvector for optimized similarity search
        dialect_name = session.bind.dialect.name if session.bind else None
        
        # Dispatch to appropriate search implementation
        if dialect_name == "postgresql":
            result = _search_pgvector(
                session=session,
                embedding_table=embedding_table,
                query_embedding=query_embedding,
                max_results=max_results,
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter,
                explain_analyse=explain_analyse,
                embedding_time=0.0,  # No embedding time when using embedding directly
                start_time=search_start,
                **kwargs
            )
        else:
            result = _search_fallback(
                session=session,
                embedding_table=embedding_table,
                query_embedding=query_embedding,
                max_results=max_results,
                source_id=source_id,
                doc_type=doc_type,
                chunking_strategy=chunking_strategy,
                filter=filter,
                embedding_time=0.0,  # No embedding time when using embedding directly
                start_time=search_start,
                **kwargs
            )
        
        session.expunge_all()
        
        # Update time_search_total
        result['metadata']['time_search_total'] = time.time() - search_start
        
        return result
        
    except Exception as e:
        logger.error(f"Error during similarity search: {e}", exc_info=True)
        raise
    finally:
        if own_session:
            session.close()


def get_similar(
    chunk_id: Optional[str] = None,
    document_id: Optional[str] = None,
    embedding_name: Optional[str] = None,
    max_results: int = 5,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    chunking_strategy: Optional[str] = None,
    filter: Optional[Dict[str, Any]] = None,
    session=None,
    **kwargs
) -> Dict[str, Any]:
    """
    Find documents similar to a given chunk or document.
    
    If chunk_id is provided, uses that chunk's embedding to find similar documents.
    If document_id is provided, recursively calls this function for each chunk in the document
    and merges the results.
    
    Exactly one of chunk_id or document_id must be provided.
    
    Args:
        chunk_id: UUID of a chunk to find similar documents for
        document_id: UUID of a document to find similar documents for
        embedding_name: Name of the embedding model to use (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        chunking_strategy: Optional filter by chunking strategy
        filter: Optional Elasticsearch-style filter query. See :func:`search` for detailed
                filter documentation and examples.
        session: Optional database session
        **kwargs: Simple metadata filters. See :func:`search` for details.
    
    Returns:
        Dictionary with same structure as :func:`get_closest`
    
    Example:
        >>> from test_mcp.kb.search.similarity import get_similar
        >>> # Find documents similar to a specific chunk
        >>> results = get_similar(
        ...     chunk_id="abc-123",
        ...     embedding_name="openai-small",
        ...     max_results=5
        ... )
        >>> # Find documents similar to a document (searches for each chunk and merges)
        >>> results = get_similar(
        ...     document_id="doc-456",
        ...     embedding_name="openai-small",
        ...     max_results=5
        ... )
    """
    from ..database import get_db_session
    from ..embedding.core import Chunk
    
    if not (chunk_id or document_id):
        raise ValueError("Either chunk_id or document_id must be provided")
    if chunk_id and document_id:
        raise ValueError("Cannot provide both chunk_id and document_id")
    
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()
    
    try:
        # Get embedding table
        config, embedding_table, embedding_name = _get_embedding_table(session, embedding_name)
        
        from sqlalchemy import select
        
        search_start = time.time()
        
        if chunk_id:
            # Get embedding for the specific chunk
            stmt = select(embedding_table.c.embedding).where(
                embedding_table.c.chunk_id == chunk_id
            )
            result = session.execute(stmt).first()
            
            if not result:
                return _empty_search_result(embedding_name, max_results, search_start)
            
            embedding = _convert_embedding_to_list(result.embedding)
            
        else:  # document_id
            # Get all chunks for this document
            chunks = session.query(Chunk).filter(Chunk.document_id == document_id).all()
            
            if not chunks:
                return _empty_search_result(embedding_name, max_results, search_start)
            
            # Recursively call get_similar for each chunk and merge results
            all_doc_results = {}  # doc_id -> {document, chunks: [best chunks]}
            
            for chunk in chunks:
                chunk_result = get_similar(
                    chunk_id=chunk.id,
                    embedding_name=embedding_name,
                    max_results=max_results * 2,  # Get more to account for merging
                    source_id=source_id,
                    doc_type=doc_type,
                    chunking_strategy=chunking_strategy,
                    filter=filter,
                    session=session,
                    **kwargs
                )
                
                # Merge results by document, keeping best similarity
                for doc_result in chunk_result['results']:
                    doc = doc_result['document']
                    if doc.id == document_id:
                        continue  # Skip the source document itself
                    
                    if doc.id not in all_doc_results:
                        all_doc_results[doc.id] = {
                            'document': doc,
                            'chunks': []
                        }
                    
                    # Add chunks from this result, keeping track of best similarity
                    for chunk_info in doc_result['chunks']:
                        # Check if we already have this chunk
                        existing_chunk = next(
                            (c for c in all_doc_results[doc.id]['chunks'] 
                             if c.get('chunk_id') == chunk_info.get('chunk_id')),
                            None
                        )
                        if existing_chunk:
                            # Keep the one with better similarity
                            if chunk_info['similarity'] > existing_chunk['similarity']:
                                all_doc_results[doc.id]['chunks'].remove(existing_chunk)
                                all_doc_results[doc.id]['chunks'].append(chunk_info)
                        else:
                            all_doc_results[doc.id]['chunks'].append(chunk_info)
            
            # Convert to list and sort by best similarity
            final_results = list(all_doc_results.values())
            for result in final_results:
                result['chunks'].sort(key=lambda x: x['similarity'], reverse=True)
            
            final_results.sort(
                key=lambda x: x['chunks'][0]['similarity'] if x['chunks'] else 0,
                reverse=True
            )
            
            # Limit to max_results
            final_results = final_results[:max_results]
            
            return {
                "results": final_results,
                "metadata": {
                    "time_search_total": time.time() - search_start,
                    "time_embedding": 0.0,
                    "time_deduplication": 0.0,
                    "total_results": len(final_results),
                    "embedding_name": embedding_name,
                    "max_results": max_results,
                }
            }
        
        # For chunk_id case, use get_closest directly
        return get_closest(
            embedding=embedding,
            embedding_name=embedding_name,
            max_results=max_results,
            source_id=source_id,
            doc_type=doc_type,
            chunking_strategy=chunking_strategy,
            filter=filter,
            session=session,
            **kwargs
        )
        
    finally:
        if own_session:
            session.close()

