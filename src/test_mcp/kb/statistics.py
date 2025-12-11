"""Statistics utilities for knowledge base."""

import logging
from typing import Optional, Dict, Any

from .database import get_db_session
from .embedding.db_models import Chunk, EmbeddingConfig, create_embedding_table
from .db_models import Document
from sqlalchemy import select, func
from sqlalchemy.inspection import inspect as sqlalchemy_inspect

logger = logging.getLogger(__name__)


def get_statistics(
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """Get statistics showing chunking strategies vs embedding names.
    
    Returns a grid where:
    - Rows are chunking strategies
    - Columns are embedding names
    - Each cell contains document count, chunk count, and embedding count for that combination
    
    Args:
        source_id: Optional filter by document source_id
        doc_type: Optional filter by document doc_type
        session: Optional database session. If None, creates a new session.
    
    Returns:
        Dictionary with structure:
        {
            "strategies": List[str],  # Row headers
            "embeddings": List[str],  # Column headers
            "data": {
                "strategy_name": {
                    "embedding_name": {
                        "documents": int,  # Distinct document count
                        "chunks": int,      # Total chunk count
                        "embeddings": int   # Total embedding count
                    }
                }
            }
        }
    
    Example:
        ```python
        from test_mcp.kb.statistics import get_statistics
        stats = get_statistics(source_id="mu2e-docdb")
        print(stats["data"]["tokens_1000_200"]["openai-small"]["documents"])
        ```
    """
    
    def _query(sess):
        # Get all strategies and embeddings
        strategies = [
            s.chunk_strategy 
            for s in sess.query(Chunk.chunk_strategy).distinct().all() 
            if s.chunk_strategy
        ]
        embedding_configs = sess.query(EmbeddingConfig).order_by(EmbeddingConfig.short_name).all()
        embedding_names = [config.short_name for config in embedding_configs]
        
        # Initialize data structure
        data = {}
        for strategy in strategies:
            data[strategy] = {}
            for embedding_name in embedding_names:
                data[strategy][embedding_name] = {
                    "documents": 0,
                    "chunks": 0,
                    "embeddings": 0
                }
        
        # Build base query for chunks with document filters
        chunk_query = sess.query(Chunk).join(Document)
        
        if source_id:
            chunk_query = chunk_query.filter(Document.source_id == source_id)
        if doc_type:
            chunk_query = chunk_query.filter(Document.doc_type == doc_type)
        
        # For each embedding, count chunks and documents
        inspector = sqlalchemy_inspect(sess.bind)
        
        def _table_exists(inspector, table_name: str) -> bool:
            """Check if a table exists in the database."""
            try:
                return table_name in inspector.get_table_names()
            except Exception:
                return False
        
        for config in embedding_configs:
            embedding_name = config.short_name
            embedding_table = create_embedding_table(config.short_name, config.dimension)
            
            # Check if table exists
            if not _table_exists(inspector, embedding_table.name):
                continue
            
            # For each strategy, count chunks and documents that have embeddings
            for strategy in strategies:
                try:
                    # Query chunks with this strategy that have embeddings
                    strategy_chunk_query = chunk_query.filter(
                        Chunk.chunk_strategy == strategy
                    )
                    
                    # Get chunk IDs that have embeddings in this embedding table
                    chunk_ids_with_embeddings = select(embedding_table.c.chunk_id).distinct()
                    chunk_ids_list = [row[0] for row in sess.execute(chunk_ids_with_embeddings).all()]
                    
                    if not chunk_ids_list:
                        continue
                    
                    # Filter chunks to only those with embeddings and apply document filters
                    chunks_with_embeddings = strategy_chunk_query.filter(
                        Chunk.id.in_(chunk_ids_list)
                    ).all()
                    
                    # Count distinct documents and total chunks
                    doc_ids = set()
                    chunk_count = 0
                    
                    for chunk in chunks_with_embeddings:
                        doc_ids.add(chunk.document_id)
                        chunk_count += 1
                    
                    # Embeddings count should equal chunks count (one embedding per chunk per embedding table)
                    # due to unique constraint on chunk_id in embedding tables
                    embedding_count = chunk_count
                    
                    data[strategy][embedding_name] = {
                        "documents": len(doc_ids),
                        "chunks": chunk_count,
                        "embeddings": embedding_count
                    }
                except Exception as e:
                    # Table might not exist or other error
                    logger.debug(f"Error querying {embedding_name} for {strategy}: {e}")
                    continue
        
        # Calculate totals across all strategies and embeddings
        total_chunks = 0
        total_embeddings = 0
        
        for strategy_data in data.values():
            for embedding_data in strategy_data.values():
                total_chunks += embedding_data["chunks"]
                total_embeddings += embedding_data["embeddings"]
        
        # Get total document count (with filters)
        doc_query = sess.query(Document)
        if source_id:
            doc_query = doc_query.filter(Document.source_id == source_id)
        if doc_type:
            doc_query = doc_query.filter(Document.doc_type == doc_type)
        total_documents_count = doc_query.count()
        
        # Count documents without chunks
        # Get all document IDs that have chunks (with filters)
        chunked_doc_ids = sess.query(Chunk.document_id).distinct()
        if source_id or doc_type:
            chunked_doc_ids = chunked_doc_ids.join(Document)
            if source_id:
                chunked_doc_ids = chunked_doc_ids.filter(Document.source_id == source_id)
            if doc_type:
                chunked_doc_ids = chunked_doc_ids.filter(Document.doc_type == doc_type)
        chunked_doc_ids_set = set(row[0] for row in chunked_doc_ids.all())
        
        # Documents without chunks = total documents - documents with chunks
        documents_without_chunks = total_documents_count - len(chunked_doc_ids_set)
        
        # Count chunks without embeddings for each embedding type
        chunks_without_embeddings = {}
        for config in embedding_configs:
            embedding_name = config.short_name
            embedding_table = create_embedding_table(config.short_name, config.dimension)
            
            # Check if table exists
            if not _table_exists(inspector, embedding_table.name):
                # If table doesn't exist, all chunks are without embeddings
                chunks_without_embeddings[embedding_name] = chunk_query.count()
                continue
            
            # Get chunk IDs that have embeddings in this embedding table
            chunk_ids_with_embeddings = select(embedding_table.c.chunk_id).distinct()
            chunk_ids_with_embeddings_list = [row[0] for row in sess.execute(chunk_ids_with_embeddings).all()]
            
            # Count chunks (with filters) that don't have embeddings
            if chunk_ids_with_embeddings_list:
                chunks_without = chunk_query.filter(
                    ~Chunk.id.in_(chunk_ids_with_embeddings_list)
                ).count()
            else:
                # If no embeddings exist, all chunks are without embeddings
                chunks_without = chunk_query.count()
            
            chunks_without_embeddings[embedding_name] = chunks_without
        
        return {
            "strategies": strategies,
            "embeddings": embedding_names,
            "data": data,
            "totals": {
                "documents": total_documents_count,
                "chunks": total_chunks,
                "embeddings": total_embeddings
            },
            "documents_without_chunks": documents_without_chunks,
            "chunks_without_embeddings": chunks_without_embeddings
        }
    
    if session is not None:
        return _query(session)
    else:
        with get_db_session() as sess:
            return _query(sess)

