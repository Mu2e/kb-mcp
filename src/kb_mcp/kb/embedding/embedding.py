"""Embedding utilities for embedding module."""

import logging
from typing import List, Optional, Dict, Any, Union
from .db_models import Chunk, EmbeddingConfig
from ..database import get_db_session
from ..database import get_db_session
from sqlalchemy import select, delete, func, inspect as sqlalchemy_inspect


# Helper functions
def _get_embedding_table(sess, embedding_name: Optional[str] = None, embedder=None):
    """Get EmbeddingConfig and table for an embedding_name, resolving default if needed."""
    from .db_models import create_embedding_table
    from .utils import get_embedding_name
    
    # Resolve embedding_name if not provided
    embedding_name = get_embedding_name(embedding_name, session=sess, embedder=embedder)
    
    config = sess.query(EmbeddingConfig).filter(
        EmbeddingConfig.short_name == embedding_name
    ).first()
    
    if not config:
        available = [c.short_name for c in sess.query(EmbeddingConfig).all()]
        raise ValueError(
            f"Embedding config '{embedding_name}' not found. "
            f"Available embeddings: {available if available else '(none)'}"
        )
    
    return config, create_embedding_table(config.short_name, config.dimension), embedding_name


def _table_exists(inspector, table_name: str) -> bool:
    """Check if a table exists in the database."""
    try:
        return table_name in inspector.get_table_names()
    except Exception:
        return False


def _convert_embedding_to_list(embedding) -> List[float]:
    """Convert embedding vector to list of floats."""
    if hasattr(embedding, 'tolist'):
        return embedding.tolist()
    elif isinstance(embedding, list):
        return embedding
    else:
        return list(embedding) if embedding else []


def embed_chunk(
    chunk: Chunk,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    session=None,
    **kwargs,
) -> List[float]:
    """
    Embed a single chunk and store it in the database.

    This is a convenience function similar to chunk_document() but for embeddings.
    Accepts either embedding_name OR (provider, model). If neither is provided, uses env vars.

    Args:
        chunk: Chunk object to embed (must have id and be saved to database)
        embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                       This should match the short_name in EmbeddingConfig and corresponds
                       to the embeddings_XXX table name.
                       If None, uses provider/model or env vars.
        provider: Optional provider name (used if embedding_name is not provided)
        model: Optional model name (used if embedding_name is not provided)
        session: Optional database session. If None, creates a new session.
        **kwargs: Additional parameters passed to embedder

    Returns:
        The embedding vector as a list of floats

    Raises:
        ValueError: If chunk doesn't have an ID or isn't saved to database

    Example:
        ```python
        from kb_mcp.kb.embedding import embed_chunk
        from kb_mcp.kb import Chunk

        chunk = Chunk.from_dict({...})
        # Save chunk first
        with get_db_session() as session:
            session.add(chunk)
            session.commit()
            # By embedding name
            embedding = embed_chunk(chunk, embedding_name="openai-small", session=session)
            # Or by provider/model
            embedding = embed_chunk(chunk, provider="openai", model="text-embedding-3-small", session=session)
            # Or use defaults from env vars
            embedding = embed_chunk(chunk, session=session)
        ```
    """
    from .utils import get_embedder

    if not chunk.id:
        raise ValueError(
            "Chunk must be saved to database first (must have id). "
            "Use session.add(chunk) and session.commit() first."
        )

    # Get embedder (will use embedding_name, provider/model, or env vars)
    embedder = get_embedder(embedding_name=embedding_name, provider=provider, model=model, session=session, **kwargs)
    
    # Determine short_name for the embedding table
    from .utils import get_embedding_name
    embedding_name = get_embedding_name(embedding_name, session=session, embedder=embedder)
    
    # Generate embeddings and store them (embed_chunks now returns the embeddings)
    embeddings = embedder.embed_chunks(
        [chunk],
        short_name=embedding_name,
        session=session,
        **kwargs
    )

    # Return the single embedding vector
    return embeddings[0]


def embed_chunks(
    chunks: List[Chunk],
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    session=None,
    **kwargs,
) -> None:
    """
    Embed multiple chunks and store them in the database.

    Accepts either embedding_name OR (provider, model). If neither is provided, uses env vars.

    Args:
        chunks: List of Chunk objects to embed (must have ids and be saved to database)
        embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                       If None, uses provider/model or env vars.
        provider: Optional provider name (used if embedding_name is not provided)
        model: Optional model name (used if embedding_name is not provided)
        batch_size: Optional batch size for embedding generation
        session: Optional database session. If None, creates a new session.
        **kwargs: Additional parameters passed to embedder

    Returns:
        None (embeddings are stored in database)

    Raises:
        ValueError: If chunks don't have IDs or aren't saved to database

    Example:
        ```python
        from kb_mcp.kb.embedding import embed_chunks
        chunks = get_chunks(document_id="abc-123")
        # By embedding name
        embed_chunks(chunks, embedding_name="openai-small", batch_size=100)
        # Or by provider/model
        embed_chunks(chunks, provider="openai", model="text-embedding-3-small", batch_size=100)
        # Or use defaults from env vars
        embed_chunks(chunks, batch_size=100)
        ```
    """
    from .utils import get_embedder

    if not chunks:
        return

    # Validate chunks have IDs
    chunks_without_id = [c for c in chunks if not c.id]
    if chunks_without_id:
        raise ValueError(
            f"{len(chunks_without_id)} chunk(s) do not have an ID. "
            "Chunks must be saved to the database before embedding. "
            "Use session.add(chunk) and session.commit() first."
        )

    # Get embedder (will use embedding_name, provider/model, or env vars)
    embedder = get_embedder(embedding_name=embedding_name, provider=provider, model=model, session=session, **kwargs)
    
    # Determine short_name for the embedding table
    from .utils import get_embedding_name
    embedding_name = get_embedding_name(embedding_name, session=session, embedder=embedder)

    # Use embedder's embed_chunks method (it will use the session if provided)
    embedder.embed_chunks(
        chunks,
        short_name=embedding_name,
        batch_size=batch_size,
        session=session,
        **kwargs
    )


def chunk_and_embed(
    document,
    chunk_strategy: Optional[str] = None,
    chunk_config: Optional[dict] = None,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    session=None,
    **kwargs,
) -> List[Chunk]:
    """Chunk a document and embed all chunks.

    This is a convenience function that combines chunk_document() and embed_chunks().
    Similar to chunk_document() but also embeds the chunks after creating them.
    If no session is provided, creates a session and uses it for both operations.

    Args:
        document: Document object to chunk and embed (must have text field and be saved to database)
        chunk_strategy: Optional chunking strategy ("tokens", "slide", or "summary").
                       If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
                       "summary" creates a single chunk from document.summary field.
        chunk_config: Optional chunking configuration. Supports embedding context flags:
                     - prepend_section_path: Prepend section_path before embedding (default: True)
                     - prepend_gist: Prepend document gist before embedding (default: True)
                     - Other strategy-specific parameters (see chunking.chunk() for details)
        embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                       If None, uses provider/model or env vars.
        provider: Optional provider name (used if embedding_name is not provided)
        model: Optional model name (used if embedding_name is not provided)
        batch_size: Optional batch size for embedding generation
        session: Optional database session. If None, creates a new session for both operations.
        **kwargs: Additional parameters passed to embedder

    Returns:
        List of Chunk objects (saved to database and embedded)

    Raises:
        ValueError: If document has no text content or isn't saved to database

    Example:
        ```python
        from kb_mcp.kb import Document
        from kb_mcp.kb.embedding import chunk_and_embed

        doc = Document.from_dict({...})
        # Save document first
        with get_db_session() as session:
            session.add(doc)
            session.commit()
            # Chunk and embed (creates its own session if not provided)
            chunks = chunk_and_embed(doc, embedding_name="openai-small")
        ```
    """
    import time
    from .chunking import chunk_document

    # Create session if not provided
    should_close = session is None

    with get_db_session(session) as session:
        # Measure chunking time
        chunk_start = time.time()
        chunks = chunk_document(
            document,
            chunk_strategy=chunk_strategy,
            config=chunk_config,
            session=session
        )
        chunk_time = time.time() - chunk_start

        # Measure embedding time and count embeddings
        embed_time = 0.0
        num_embeddings = 0
        if chunks:
            embed_start = time.time()
            embed_chunks(
                chunks,
                embedding_name=embedding_name,
                provider=provider,
                model=model,
                batch_size=batch_size,
                session=session,
                **kwargs
            )
            embed_time = time.time() - embed_start

            # Count embeddings created (one per chunk if embedding succeeded)
            # Since embed_chunks succeeded, we assume one embedding per chunk
            # For accuracy, we could query the embedding table, but this is simpler and sufficient
            num_embeddings = len(chunks)

        # Create log entry for this operation
        from .db_models import ChunkEmbeddingLog
        import socket

        total_time = chunk_time + embed_time
        hostname = socket.gethostname()

        # Get actual chunk strategy from first chunk (includes any suffix like _no_context)
        actual_chunk_strategy = chunks[0].chunk_strategy if chunks else chunk_strategy

        log_entry = ChunkEmbeddingLog(
            document_id=document.id,
            chunking_time_seconds=round(chunk_time, 3),
            embedding_time_seconds=round(embed_time, 3),
            total_time_seconds=round(total_time, 3),
            num_chunks=len(chunks) if chunks else 0,
            num_embeddings=num_embeddings,
            chunk_strategy=actual_chunk_strategy,
            embedding_name=embedding_name or (provider and model and f"{provider}/{model}") or None,
            hostname=hostname,
        )
        session.add(log_entry)

        # Commit if we created the session
        if should_close:
            session.commit()
            # Refresh and expunge chunks so they can be used after session closes
            for chunk in chunks:
                session.refresh(chunk)
                

        return chunks


def get_embedding_names(session=None) -> List[Dict[str, Any]]:
    """Get all embedding names/configurations with counts of embedded chunks.

    Similar to get_chunk_strategies(), but for embeddings.
    Shows which embedding models are available and how many chunks have embeddings for each.

    Args:
        session: Optional database session. If None, creates a new session.

    Returns:
        List of dictionaries with:
        - short_name: str - The embedding config short name (used in embeddings_XXX table names)
        - provider: str - The embedding provider
        - model: str - The model name
        - dimension: int - Embedding dimension
        - table_name: str - The embedding table name
        - count: int - Number of chunks with embeddings for this config
        - created_time: str - ISO format timestamp
        - meta: dict - Additional metadata
    """
    from .db_models import create_embedding_table

    def _query(sess):
        # Get all embedding configs
        configs = sess.query(EmbeddingConfig).order_by(EmbeddingConfig.short_name).all()
        
        results = []
        for config in configs:
            # Get the embedding table for this config
            embedding_table = create_embedding_table(config.short_name, config.dimension)
            
            # Count embeddings in this table
            try:
                inspector = sqlalchemy_inspect(sess.bind)
                if _table_exists(inspector, embedding_table.name):
                    count_stmt = select(func.count(embedding_table.c.id))
                    count = sess.execute(count_stmt).scalar() or 0
                else:
                    count = 0
            except Exception:
                count = 0
            
            config_dict = config.to_dict()
            config_dict['count'] = count
            results.append(config_dict)
        
        return results

    if session is not None:
        return _query(session)
    else:
        with get_db_session() as sess:
            return _query(sess)


def drop_embedding_table(
    embedding_name: str,
) -> Dict[str, Any]:
    """Drop an embedding table and remove the corresponding EmbeddingConfig entry.

    This completely removes an embedding type from the database:
    - Drops the embedding table (e.g., embeddings_openai-small)
    - Deletes the EmbeddingConfig entry

    Args:
        embedding_name: Short name of the embedding config (e.g., "openai-small")
                       This should match the short_name in EmbeddingConfig.

    Returns:
        Dictionary with:
        - embedding_name: str - The embedding name that was dropped
        - table_name: str - The table name that was dropped
        - count: int - Number of embeddings that were in the table (before dropping)
        - success: bool - Whether the operation succeeded

    Raises:
        ValueError: If embedding_name is not found in EmbeddingConfig

    Example:
        ```python
        from kb_mcp.kb.embedding import drop_embedding_table
        result = drop_embedding_table("openai-small")
        print(f"Dropped {result['count']} embeddings from {result['table_name']}")
        ```
    """
    from .db_models import get_embedding_table_name
    import logging

    logger = logging.getLogger(__name__)

    with get_db_session() as sess:
        config, embedding_table, _ = _get_embedding_table(sess, embedding_name)
        table_name = get_embedding_table_name(config.short_name)

        # Count embeddings before dropping
        count = 0
        try:
            inspector = sqlalchemy_inspect(sess.bind)
            if _table_exists(inspector, embedding_table.name):
                count_stmt = select(func.count(embedding_table.c.id))
                count = sess.execute(count_stmt).scalar() or 0
        except Exception:
            pass

        # Drop the table if it exists
        try:
            inspector = sqlalchemy_inspect(sess.bind)
            if _table_exists(inspector, embedding_table.name):
                embedding_table.drop(bind=sess.bind, checkfirst=True)
                sess.flush()
        except Exception as e:
            logger.warning(f"Error dropping table {table_name}: {e}")

        # Delete the EmbeddingConfig entry
        sess.delete(config)
        sess.flush()
        sess.commit()

        return {
            "embedding_name": embedding_name,
            "table_name": table_name,
            "count": count,
            "success": True,
        }


def drop_embedding(
    chunk_id: str,
    embedding_name: Optional[str] = None,
) -> int:
    """Drop embedding(s) for a specific chunk.

    Args:
        chunk_id: UUID of the chunk
        embedding_name: Optional short name of the embedding config (e.g., "openai-small").
                       If provided, only drops embedding from that specific table.
                       If None, drops embeddings from ALL embedding tables for this chunk.

    Returns:
        Number of embeddings deleted

    Example:
        ```python
        from kb_mcp.kb.embedding import drop_embedding
        # Drop from specific embedding table
        count = drop_embedding("chunk-123", embedding_name="openai-small")
        print(f"Deleted {count} embedding(s)")
        # Drop from all embedding tables
        count = drop_embedding("chunk-123")
        print(f"Deleted {count} embedding(s) from all tables")
        ```
    """
    with get_db_session() as sess:
        total_deleted = 0
        inspector = sqlalchemy_inspect(sess.bind)

        if embedding_name is not None:
            # Delete from specific embedding table
            _, embedding_table, _ = _get_embedding_table(sess, embedding_name)
            if _table_exists(inspector, embedding_table.name):
                stmt = delete(embedding_table).where(
                    embedding_table.c.chunk_id == chunk_id
                )
                result = sess.execute(stmt)
                total_deleted = result.rowcount
        else:
            # Delete from all embedding tables
            configs = sess.query(EmbeddingConfig).all()
            for config in configs:
                from .db_models import create_embedding_table
                embedding_table = create_embedding_table(config.short_name, config.dimension)
                try:
                    if _table_exists(inspector, embedding_table.name):
                        stmt = delete(embedding_table).where(
                            embedding_table.c.chunk_id == chunk_id
                        )
                        result = sess.execute(stmt)
                        total_deleted += result.rowcount
                except Exception:
                    pass

        sess.flush()
        sess.commit()
        return total_deleted


def get_embeddings(
    chunk_id: str,
    embedding_name: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Get embedding(s) for a chunk with metadata.

    Args:
        chunk_id: UUID of the chunk
        embedding_name: Optional short name of the embedding config (e.g., "openai-small").
                       If provided, returns dict with only that embedding.
                       If None, returns dict with all embeddings for this chunk.

    Returns:
        Dict mapping embedding_name to dict with:
        - id: str - The embedding record ID
        - embedding: List[float] - The embedding vector
        Empty dict if no embeddings found

    Example:
        ```python
        from kb_mcp.kb.embedding import get_embeddings
        # Get specific embedding
        result = get_embeddings("chunk-123", embedding_name="openai-small")
        # Returns: {"openai-small": {"id": "...", "embedding": [0.1, 0.2, ...]}}
        # Get all embeddings
        all_embeddings = get_embeddings("chunk-123")
        # Returns: {"openai-small": {"id": "...", "embedding": [...]}, "st-minilm": {...}}
        ```
    """
    from .db_models import create_embedding_table

    with get_db_session() as sess:
        inspector = sqlalchemy_inspect(sess.bind)
        embeddings_dict = {}

        if embedding_name is not None:
            # Get specific embedding
            try:
                _, embedding_table, _ = _get_embedding_table(sess, embedding_name)
                if _table_exists(inspector, embedding_table.name):
                    stmt = select(embedding_table.c.id, embedding_table.c.embedding).where(
                        embedding_table.c.chunk_id == chunk_id
                    ).limit(1)
                    result = sess.execute(stmt).first()
                    if result is not None:
                        embeddings_dict[embedding_name] = {
                            "id": result[0],
                            "embedding": _convert_embedding_to_list(result[1])
                        }
            except (ValueError, Exception):
                pass
        else:
            # Get all embeddings
            configs = sess.query(EmbeddingConfig).all()
            for config in configs:
                embedding_table = create_embedding_table(config.short_name, config.dimension)
                try:
                    if _table_exists(inspector, embedding_table.name):
                        stmt = select(embedding_table.c.id, embedding_table.c.embedding).where(
                            embedding_table.c.chunk_id == chunk_id
                        ).limit(1)
                        result = sess.execute(stmt).first()
                        if result is not None:
                            embeddings_dict[config.short_name] = {
                                "id": result[0],
                                "embedding": _convert_embedding_to_list(result[1])
                            }
                except Exception:
                    pass

        return embeddings_dict


def get_embedding_vector(
    chunk_id: str,
    embedding_name: str,
) -> Optional[List[float]]:
    """Get a single embedding vector for a chunk.

    Args:
        chunk_id: UUID of the chunk
        embedding_name: Short name of the embedding config (e.g., "openai-small").
                       Required.

    Returns:
        The embedding vector (List[float]) or None if not found

    Example:
        ```python
        from kb_mcp.kb.embedding import get_embedding_vector
        embedding = get_embedding_vector("chunk-123", embedding_name="openai-small")
        # Returns: [0.1, 0.2, ...] or None
        ```
    """
    from .db_models import create_embedding_table

    with get_db_session() as sess:
        inspector = sqlalchemy_inspect(sess.bind)

        try:
            _, embedding_table, _ = _get_embedding_table(sess, embedding_name)
            if not _table_exists(inspector, embedding_table.name):
                return None

            stmt = select(embedding_table.c.embedding).where(
                embedding_table.c.chunk_id == chunk_id
            ).limit(1)
            result = sess.execute(stmt).first()
            if result is None:
                return None
            return _convert_embedding_to_list(result[0])
        except (ValueError, Exception):
            return None


def optimize_embedding_index(embedding_name: str, session=None) -> Dict[str, Any]:
    """
    Optimize IVFFlat index for an embedding table by recalculating optimal 'lists' parameter.
    
    IVFFlat indexes work best when the 'lists' parameter is optimized based on the number
    of embeddings. This function:
    1. Counts embeddings in the table
    2. Calculates optimal 'lists' parameter (roughly sqrt(count) or count/1000, min 10, max 1000)
    3. Rebuilds the index with the optimized parameter
    
    Args:
        embedding_name: Short name of the embedding (e.g., "openai-small")
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Dictionary with optimization results:
        {
            "embedding_name": str,
            "embedding_count": int,
            "optimal_lists": int,
            "index_rebuilt": bool,
            "message": str
        }
        
    Example:
        ```python
        from kb_mcp.kb.embedding import optimize_embedding_index
        result = optimize_embedding_index("openai-small")
        print(result["message"])
        ```
    """
    from sqlalchemy import text
    
    if session is None:
        session = get_db_session()
        should_close = True
    else:
        should_close = False
    
    try:
        config, embedding_table, embedding_name = _get_embedding_table(session, embedding_name)
        dialect_name = session.bind.dialect.name
        
        if dialect_name != 'postgresql':
            return {
                "embedding_name": embedding_name,
                "embedding_count": 0,
                "optimal_lists": None,
                "index_rebuilt": False,
                "message": f"Index optimization only supported for PostgreSQL (current: {dialect_name})"
            }
        
        inspector = sqlalchemy_inspect(session.bind)
        if not _table_exists(inspector, embedding_table.name):
            return {
                "embedding_name": embedding_name,
                "embedding_count": 0,
                "optimal_lists": None,
                "index_rebuilt": False,
                "message": f"Table {embedding_table.name} does not exist"
            }
        
        # Count embeddings
        count_stmt = select(func.count()).select_from(embedding_table)
        embedding_count = session.execute(count_stmt).scalar() or 0
        
        if embedding_count == 0:
            return {
                "embedding_name": embedding_name,
                "embedding_count": 0,
                "optimal_lists": None,
                "index_rebuilt": False,
                "message": f"No embeddings found in {embedding_table.name}"
            }
        
        # Calculate optimal lists parameter
        # Rule of thumb: lists = sqrt(count) or count/1000, with bounds
        # pgvector docs recommend: lists = rows / 1000 for up to 1M rows, then rows / sqrt(rows)
        if embedding_count <= 1000:
            optimal_lists = max(10, embedding_count // 100)  # At least 10, roughly 1% of count
        elif embedding_count <= 1000000:
            optimal_lists = min(1000, max(10, embedding_count // 1000))  # rows / 1000, capped at 1000
        else:
            import math
            optimal_lists = min(1000, max(10, int(math.sqrt(embedding_count))))  # sqrt(rows), capped at 1000
        
        index_name = f"{embedding_table.name}_vector_idx"
        
        # Check current index
        indexes = inspector.get_indexes(embedding_table.name)
        index_exists = any(idx['name'] == index_name for idx in indexes)
        
        if not index_exists:
            # Create index if it doesn't exist
            try:
                # Quote identifiers to handle special characters like hyphens
                session.execute(text(f"""
                    CREATE INDEX "{index_name}"
                    ON "{embedding_table.name}"
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = {optimal_lists})
                """))
                session.commit()
                return {
                    "embedding_name": embedding_name,
                    "embedding_count": embedding_count,
                    "optimal_lists": optimal_lists,
                    "index_rebuilt": True,
                    "message": f"Created IVFFlat index with lists={optimal_lists} for {embedding_count} embeddings"
                }
            except Exception as e:
                return {
                    "embedding_name": embedding_name,
                    "embedding_count": embedding_count,
                    "optimal_lists": optimal_lists,
                    "index_rebuilt": False,
                    "message": f"Failed to create index: {e}"
                }

        # Rebuild index with optimized parameter
        try:
            # Drop and recreate index
            # Quote identifiers to handle special characters like hyphens
            session.execute(text(f'DROP INDEX IF EXISTS "{index_name}"'))
            session.execute(text(f"""
                CREATE INDEX "{index_name}"
                ON "{embedding_table.name}"
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = {optimal_lists})
            """))
            session.commit()
            
            return {
                "embedding_name": embedding_name,
                "embedding_count": embedding_count,
                "optimal_lists": optimal_lists,
                "index_rebuilt": True,
                "message": f"Rebuilt IVFFlat index with lists={optimal_lists} for {embedding_count} embeddings"
            }
        except Exception as e:
            session.rollback()
            return {
                "embedding_name": embedding_name,
                "embedding_count": embedding_count,
                "optimal_lists": optimal_lists,
                "index_rebuilt": False,
                "message": f"Failed to rebuild index: {e}"
            }
    finally:
        if should_close:
            session.close()


def vacuum_analyze_embedding_table(embedding_name: str, session=None) -> Dict[str, Any]:
    """
    Run VACUUM ANALYZE on an embedding table to update PostgreSQL statistics.
    
    This is important for query planning, especially after bulk inserts or updates.
    VACUUM ANALYZE updates table statistics that PostgreSQL uses to choose optimal query plans.
    
    Args:
        embedding_name: Short name of the embedding (e.g., "openai-small")
        session: Optional database session. If None, creates a new session.
        
    Returns:
        Dictionary with results:
        {
            "embedding_name": str,
            "vacuumed": bool,
            "message": str
        }
        
    Example:
        ```python
        from kb_mcp.kb.embedding import vacuum_analyze_embedding_table
        result = vacuum_analyze_embedding_table("openai-small")
        ```
    """
    from sqlalchemy import text
    
    if session is None:
        session = get_db_session()
        should_close = True
    else:
        should_close = False
    
    try:
        config, embedding_table, embedding_name = _get_embedding_table(session, embedding_name)
        dialect_name = session.bind.dialect.name
        
        if dialect_name != 'postgresql':
            return {
                "embedding_name": embedding_name,
                "vacuumed": False,
                "message": f"VACUUM ANALYZE only supported for PostgreSQL (current: {dialect_name})"
            }
        
        inspector = sqlalchemy_inspect(session.bind)
        if not _table_exists(inspector, embedding_table.name):
            return {
                "embedding_name": embedding_name,
                "vacuumed": False,
                "message": f"Table {embedding_table.name} does not exist"
            }
        
        try:
            # VACUUM ANALYZE updates statistics for query planning
            session.execute(text(f"VACUUM ANALYZE {embedding_table.name}"))
            session.commit()
            
            return {
                "embedding_name": embedding_name,
                "vacuumed": True,
                "message": f"VACUUM ANALYZE completed for {embedding_table.name}"
            }
        except Exception as e:
            session.rollback()
            return {
                "embedding_name": embedding_name,
                "vacuumed": False,
                "message": f"Failed to run VACUUM ANALYZE: {e}"
            }
    finally:
        if should_close:
            session.close()

