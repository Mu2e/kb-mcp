"""Search functionality for knowledge base using vector embeddings."""

import logging
import time
from typing import List, Optional, Dict, Any, Tuple
import numpy as np

from .database import get_db_session
from .core import Document
from .embedding.core import Chunk, EmbeddingConfig
from .embedding.embedding import _get_embedding_table, _convert_embedding_to_list
from .embedding.utils import get_embedder
from sqlalchemy import select, func, and_, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

logger = logging.getLogger(__name__)


def build_document_filters(
    doc_alias: Any,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    **kwargs
) -> List[Any]:
    """
    Build SQLAlchemy filter conditions for document queries.
    
    This is a reusable helper function for building filters that can be applied
    to document queries across different parts of the codebase.
    
    Args:
        doc_alias: SQLAlchemy aliased Document model (or Document class)
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        **kwargs: Additional filters:
            - meta_*: Filter by metadata fields (e.g., meta_author="John")
            - Other kwargs are ignored
    
    Returns:
        List of SQLAlchemy filter conditions (can be combined with and_() or or_())
    
    Example:
        >>> from sqlalchemy.orm import aliased
        >>> from test_mcp.kb.core import Document
        >>> doc_alias = aliased(Document)
        >>> filters = build_document_filters(
        ...     doc_alias,
        ...     source_id="atlas-docdb",
        ...     doc_type="paper",
        ...     meta_author="John Doe"
        ... )
        >>> query = query.where(and_(*filters))
    """
    filters = []
    
    if source_id:
        filters.append(doc_alias.source_id == source_id)
    
    if doc_type:
        filters.append(doc_alias.doc_type == doc_type)
    
    # Additional metadata filters from kwargs
    for key, value in kwargs.items():
        if key.startswith('meta_'):
            meta_key = key[5:]  # Remove 'meta_' prefix
            # Use JSON path query for metadata
            # For PostgreSQL, use -> operator; for SQLite, use json_extract
            try:
                # Try PostgreSQL JSON operator first
                filters.append(
                    doc_alias.meta[meta_key].astext == str(value)
                )
            except (AttributeError, TypeError):
                # Fallback to json_extract for SQLite
                filters.append(
                    func.json_extract(doc_alias.meta, f'$.{meta_key}') == value
                )
    
    return filters


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)
    
    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    
    if norm1 == 0 or norm2 == 0:
        return 0.0
    
    return float(dot_product / (norm1 * norm2))


def search(
    query: str,
    embedding_name: Optional[str] = None,
    max_results: int = 10,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    session=None,
    explain_analyse: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """
    Search for documents using vector similarity.
    
    Args:
        query: Search query text
        embedding_name: Name of the embedding to use (e.g., "openai-small").
                       If None, uses default from environment variables.
        max_results: Maximum number of unique documents to return
        source_id: Optional filter by source ID
        doc_type: Optional filter by document type
        session: Optional database session
        explain_analyse: If True, print EXPLAIN ANALYZE output for the query (PostgreSQL only)
        **kwargs: Additional filters (e.g., metadata filters)
    
    Returns:
        Dictionary containing:
        - results: List of dictionaries, each containing:
            - document: Document object
            - chunks: List of matching chunks with their distances
            - best_distance: Best (highest) cosine similarity score for this document
            - best_chunk: The chunk with the best similarity score
        - metadata: Dictionary with search metadata:
            - search_time: Time taken to execute the search (in seconds)
            - total_results: Number of documents returned
            - query: The original search query
            - embedding_name: The embedding model used
            - max_results: Maximum results requested
            # Future fields can be added here (e.g., summary, filters_applied, etc.)
    
    Example:
        >>> from test_mcp.kb import search
        >>> response = search("track reconstruction", embedding_name="openai-small", max_results=5)
        >>> print(f"Search took {response['metadata']['search_time']:.3f}s")
        >>> for result in response['results']:
        ...     print(f"Document: {result['document'].id}, Score: {result['best_distance']:.3f}")
    """
    # Start timing the search
    start_time = time.time()
    
    # Store original embedding_name for metadata (will be updated if determined from embedder)
    used_embedding_name = embedding_name
    
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()
    
    try:
        # Get embedder and embed the query
        embedder = get_embedder(embedding_name=embedding_name, session=session, **kwargs)
        query_embedding = embedder([query])[0]  # Get first (and only) embedding
        query_embedding = _convert_embedding_to_list(query_embedding)
        
        # Get embedding table
        if embedding_name is None:
            # Generate embedding_name from embedder
            embedding_name = embedder._generate_short_name()
        
        # Store embedding_name for metadata (use the determined value)
        used_embedding_name = embedding_name
        
        config, embedding_table = _get_embedding_table(session, embedding_name)
        
        # Build base query: embeddings -> chunks -> documents
        chunk_alias = aliased(Chunk)
        doc_alias = aliased(Document)
        
        # Start with embeddings table
        query_stmt = select(
            embedding_table.c.id.label('embedding_id'),
            embedding_table.c.chunk_id,
            embedding_table.c.embedding,
            chunk_alias.id.label('chunk_id_full'),
            chunk_alias.document_id,
            chunk_alias.chunk_index,
            chunk_alias.char_start_index,
            chunk_alias.char_end_index,
            chunk_alias.token_length,
            doc_alias.id.label('doc_id'),
            doc_alias.source_id,
            doc_alias.doc_type,
            doc_alias.doc_id.label('doc_doc_id'),
            doc_alias.meta,
        ).join(
            chunk_alias, embedding_table.c.chunk_id == chunk_alias.id
        ).join(
            doc_alias, chunk_alias.document_id == doc_alias.id
        )
        
        # Apply filters using helper function
        filters = build_document_filters(
            doc_alias,
            source_id=source_id,
            doc_type=doc_type,
            **kwargs
        )
        
        if filters:
            query_stmt = query_stmt.where(and_(*filters))
        
        # Check if we're using PostgreSQL with pgvector for optimized similarity search
        dialect_name = session.bind.dialect.name if session.bind else None
        use_pgvector = dialect_name == "postgresql"
        
        # Convert query embedding to a format suitable for pgvector
        # pgvector expects a list/array that can be cast to vector type
        query_embedding_for_db = query_embedding
        
        if use_pgvector:
            try:
                # Use pgvector's native cosine distance operator (<=>)
                # Following the pattern from chATLAS_Embed VectorStores.py (line ~2227)
                # Uses CTEs with DISTINCT ON for efficient deduplication
                from sqlalchemy import text
                
                # Convert query embedding to PostgreSQL vector format
                query_vec_str = '[' + ','.join(str(x) for x in query_embedding) + ']'
                
                # Build WHERE clause from filter parameters
                # Similar to _generate_metadata_filters in the reference implementation
                # We build the WHERE clause manually based on the filter parameters
                # This is more reliable than trying to convert SQLAlchemy filter objects
                where_parts = []
                filter_params_dict = {}
                
                # Handle source_id filter
                if source_id:
                    where_parts.append("d.source_id = :source_id")
                    filter_params_dict["source_id"] = source_id
                
                # Handle doc_type filter
                if doc_type:
                    where_parts.append("d.doc_type = :doc_type")
                    filter_params_dict["doc_type"] = doc_type
                
                # Handle metadata filters from kwargs
                # Following the pattern from _generate_metadata_filters in the reference
                for key, value in kwargs.items():
                    if key.startswith('meta_'):
                        meta_key = key[5:]  # Remove 'meta_' prefix
                        param_name = f"meta_{meta_key}"
                        
                        # Handle different value types
                        if isinstance(value, list):
                            # For arrays, check if any value in the list matches
                            # Use JSONB ?| operator (contains any of the array elements)
                            where_parts.append(f"CAST(d.meta::jsonb->:meta_key_{meta_key} AS jsonb) ?| :{param_name}")
                            filter_params_dict[f"meta_key_{meta_key}"] = meta_key
                            filter_params_dict[param_name] = [str(v) for v in value]
                        else:
                            # For single values, use -> operator
                            where_parts.append(f"d.meta->>'{meta_key}' = :{param_name}")
                            filter_params_dict[param_name] = str(value)
                
                where_clause_sql = " AND ".join(where_parts) if where_parts else "TRUE"
                
                # Get more chunks initially to account for deduplication
                # Following the pattern: get k * 10 chunks, then deduplicate to k documents
                initial_limit = max_results * 10
                
                # Build query using CTEs (Common Table Expressions) for efficiency
                # This pattern:
                # 1. Calculates similarity scores with filters applied early (LIMIT applied here)
                # 2. Uses DISTINCT ON to get best chunk per document
                # 3. Joins with parent documents and limits final results
                
                vector_query = f"""
                    WITH similarity_scores AS (
                        SELECT
                            c.id AS chunk_id,
                            c.document_id AS parent_id,
                            c.chunk_index,
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
                    ),
                    best_chunks AS (
                        SELECT DISTINCT ON (parent_id)
                            chunk_id,
                            parent_id,
                            chunk_index,
                            char_start_index,
                            char_end_index,
                            token_length,
                            score
                        FROM similarity_scores
                        ORDER BY parent_id, score DESC
                    )
                    SELECT 
                        bc.chunk_id,
                        bc.parent_id,
                        bc.chunk_index,
                        bc.char_start_index,
                        bc.char_end_index,
                        bc.token_length,
                        bc.score,
                        d.id AS doc_id,
                        d.source_id,
                        d.doc_type,
                        d.meta
                    FROM best_chunks bc
                    JOIN documents d ON bc.parent_id = d.id
                    ORDER BY bc.score DESC
                    LIMIT :max_results
                """
                
                # Set PostgreSQL session parameters for optimal query performance
                # Following the pattern from chATLAS_Embed VectorStores.py
                # These settings help PostgreSQL choose better query plans
                search_hyperparams = """
                    SET enable_seqscan = OFF;
                    SET plan_cache_mode = force_generic_plan;
                """
                session.execute(text(search_hyperparams))
                
                # Set IVFFlat probes for approximate nearest neighbor search
                # Higher probes = more accurate but slower
                # Default is usually 1, but 32 is a good balance for most cases
                # This can be tuned based on your accuracy/speed requirements
                try:
                    session.execute(text("SET ivfflat.probes = 32"))
                except Exception as e:
                    # If ivfflat extension isn't configured or probes setting fails, continue
                    logger.debug(f"Could not set ivfflat.probes (this is OK if not using IVFFlat index): {e}")
                
                # Execute query with parameters
                query_params = {
                    "query_embedding": query_vec_str,
                    "initial_limit": initial_limit,
                    "max_results": max_results,
                    **filter_params_dict,
                }
                
                # If explain_analyse is enabled, run EXPLAIN ANALYZE first
                if explain_analyse:
                    explain_query = f"EXPLAIN (ANALYSE, BUFFERS) {vector_query}"
                    explain_results = session.execute(text(explain_query), query_params)
                    explain_output = []
                    print("\n=== Query Execution Plan ===")
                    for row in explain_results:
                        plan_line = row[0] if isinstance(row, tuple) else str(row)
                        explain_output.append(plan_line)
                        print(plan_line)
                    print("===========================\n")
                    logger.info("Query execution plan:\n" + "\n".join(explain_output))
                
                results = session.execute(text(vector_query), query_params).all()
                
                scored_results = []
                for row in results:
                    scored_results.append({
                        'similarity': float(row.score),
                        'embedding_id': None,  # Not needed for final results
                        'chunk_id': row.chunk_id,
                        'document_id': row.doc_id,
                        'chunk_index': row.chunk_index,
                        'char_start': row.char_start_index,
                        'char_end': row.char_end_index,
                        'token_length': row.token_length,
                        'source_id': row.source_id,
                        'doc_type': row.doc_type,
                    })
                
                logger.debug(f"Using pgvector native operators with CTEs, retrieved {len(scored_results)} results")
                
                # Results are already deduplicated by DISTINCT ON, so we can skip the Python deduplication
                # Just need to load documents and format results
                unique_doc_ids = list(set(r['document_id'] for r in scored_results))
                documents_by_id = {
                    doc.id: doc
                    for doc in session.query(Document).filter(Document.id.in_(unique_doc_ids)).all()
                }
                
                doc_results = {}
                for result in scored_results:
                    doc_id = result['document_id']
                    
                    if doc_id not in doc_results:
                        doc = documents_by_id.get(doc_id)
                        if not doc:
                            continue
                        
                        doc_results[doc_id] = {
                            'document': doc,
                            'chunks': [],
                            'best_distance': result['similarity'],
                            'best_chunk': None,
                        }
                    
                    chunk_info = {
                        'chunk_id': result['chunk_id'],
                        'chunk_index': result['chunk_index'],
                        'similarity': result['similarity'],
                        'char_start': result['char_start'],
                        'char_end': result['char_end'],
                        'token_length': result['token_length'],
                    }
                    
                    doc_results[doc_id]['chunks'].append(chunk_info)
                    
                    # Update best chunk if this one is better or equal (handles first chunk case)
                    if result['similarity'] >= doc_results[doc_id]['best_distance']:
                        doc_results[doc_id]['best_distance'] = result['similarity']
                        doc_results[doc_id]['best_chunk'] = chunk_info
                
                # Convert to list and sort by best_distance (descending)
                final_results = list(doc_results.values())
                final_results.sort(key=lambda x: x['best_distance'], reverse=True)
                
                # Sort chunks within each document by similarity (descending)
                for result in final_results:
                    result['chunks'].sort(key=lambda x: x['similarity'], reverse=True)
                
                # Calculate search time and wrap results with metadata
                search_time = time.time() - start_time
                return {
                    'results': final_results,
                    'metadata': {
                        'search_time': search_time,
                        'total_results': len(final_results),
                        'query': query,
                        'embedding_name': used_embedding_name,
                        'max_results': max_results,
                    }
                }
                
            except (ImportError, AttributeError, Exception) as e:
                logger.debug(f"pgvector optimization failed ({e}), falling back to Python calculation")
                use_pgvector = False
        
        if not use_pgvector:
            # Fallback: Get all embeddings and calculate similarity in Python
            # For SQLite or when pgvector is not available
            # Note: query_stmt already has filters applied via SQLAlchemy ORM,
            # which works with both SQLite and PostgreSQL
            results = session.execute(query_stmt).all()
            
            if not results:
                # Calculate search time and return empty results with metadata
                search_time = time.time() - start_time
                return {
                    'results': [],
                    'metadata': {
                        'search_time': search_time,
                        'total_results': 0,
                        'query': query,
                        'embedding_name': used_embedding_name,
                        'max_results': max_results,
                    }
                }
            
            # Calculate cosine similarity for each embedding
            # Using numpy for efficient vector operations
            query_vec = np.array(query_embedding, dtype=np.float32)
            query_norm = np.linalg.norm(query_vec)
            
            scored_results = []
            for row in results:
                embedding_vector = _convert_embedding_to_list(row.embedding)
                emb_vec = np.array(embedding_vector, dtype=np.float32)
                
                # Optimized cosine similarity calculation
                dot_product = np.dot(query_vec, emb_vec)
                emb_norm = np.linalg.norm(emb_vec)
                
                if query_norm == 0 or emb_norm == 0:
                    similarity = 0.0
                else:
                    similarity = float(dot_product / (query_norm * emb_norm))
                
                # Store result with similarity score
                scored_results.append({
                    'similarity': similarity,
                    'embedding_id': row.embedding_id,
                    'chunk_id': row.chunk_id,
                    'document_id': row.doc_id,
                    'chunk_index': row.chunk_index,
                    'char_start': row.char_start_index,
                    'char_end': row.char_end_index,
                    'token_length': row.token_length,
                    'source_id': row.source_id,
                    'doc_type': row.doc_type,
                })
            
            # Sort by similarity (descending - highest first)
            scored_results.sort(key=lambda x: x['similarity'], reverse=True)
        
        # Group by document and keep best chunks per document
        # Performance: Batch load all unique documents in one query
        unique_doc_ids = list(set(r['document_id'] for r in scored_results))
        documents_by_id = {
            doc.id: doc
            for doc in session.query(Document).filter(Document.id.in_(unique_doc_ids)).all()
        }
        
        doc_results = {}  # document_id -> {document, chunks, best_distance, best_chunk}
        
        for result in scored_results:
            doc_id = result['document_id']
            
            if doc_id not in doc_results:
                doc = documents_by_id.get(doc_id)
                if not doc:
                    continue
                
                doc_results[doc_id] = {
                    'document': doc,
                    'chunks': [],
                    'best_distance': result['similarity'],
                    'best_chunk': None,
                }
            
            # Add chunk info
            chunk_info = {
                'chunk_id': result['chunk_id'],
                'chunk_index': result['chunk_index'],
                'similarity': result['similarity'],
                'char_start': result['char_start'],
                'char_end': result['char_end'],
                'token_length': result['token_length'],
            }
            
            doc_results[doc_id]['chunks'].append(chunk_info)
            
            # Update best chunk if this one is better or equal (handles first chunk case)
            if result['similarity'] >= doc_results[doc_id]['best_distance']:
                doc_results[doc_id]['best_distance'] = result['similarity']
                doc_results[doc_id]['best_chunk'] = chunk_info
        
        # Convert to list and sort by best_distance (descending)
        final_results = list(doc_results.values())
        final_results.sort(key=lambda x: x['best_distance'], reverse=True)
        
        # Limit to max_results
        final_results = final_results[:max_results]
        
        # Sort chunks within each document by similarity (descending)
        for result in final_results:
            result['chunks'].sort(key=lambda x: x['similarity'], reverse=True)
        
        # Calculate search time and wrap results with metadata
        search_time = time.time() - start_time
        return {
            'results': final_results,
            'metadata': {
                'search_time': search_time,
                'total_results': len(final_results),
                'query': query,
                'embedding_name': used_embedding_name,
                'max_results': max_results,
            }
        }
        
    except Exception as e:
        logger.error(f"Error during search: {e}", exc_info=True)
        raise
    finally:
        if own_session:
            session.close()

