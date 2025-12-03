"""Embedding utilities for embedding module."""

from typing import List, Optional, Dict, Any, Union
from .core import Chunk, EmbeddingConfig
from ..database import get_db_session
from sqlalchemy import select, delete, func, inspect as sqlalchemy_inspect


# Helper functions
def _get_embedding_table(sess, embedding_name: str):
    """Get EmbeddingConfig and table for an embedding_name."""
    from .core import create_embedding_table
    
    config = sess.query(EmbeddingConfig).filter(
        EmbeddingConfig.short_name == embedding_name
    ).first()
    
    if not config:
        available = [c.short_name for c in sess.query(EmbeddingConfig).all()]
        raise ValueError(
            f"Embedding config '{embedding_name}' not found. "
            f"Available embeddings: {available if available else '(none)'}"
        )
    
    return config, create_embedding_table(config.short_name, config.dimension)


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
        >>> from test_mcp.kb.embedding import embed_chunk
        >>> from test_mcp.kb import Chunk
        >>>
        >>> chunk = Chunk.from_dict({...})
        >>> # Save chunk first
        >>> with get_db_session() as session:
        ...     session.add(chunk)
        ...     session.commit()
        ...     # By embedding name
        ...     embedding = embed_chunk(chunk, embedding_name="openai-small", session=session)
        ...     # Or by provider/model
        ...     embedding = embed_chunk(chunk, provider="openai", model="text-embedding-3-small", session=session)
        ...     # Or use defaults from env vars
        ...     embedding = embed_chunk(chunk, session=session)
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
    # If embedding_name provided, use it; otherwise get from embedder
    if embedding_name is None:
        embedding_name = embedder._generate_short_name()
    
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
        >>> from test_mcp.kb.embedding import embed_chunks
        >>> chunks = get_chunks(document_id="abc-123")
        >>> # By embedding name
        >>> embed_chunks(chunks, embedding_name="openai-small", batch_size=100)
        >>> # Or by provider/model
        >>> embed_chunks(chunks, provider="openai", model="text-embedding-3-small", batch_size=100)
        >>> # Or use defaults from env vars
        >>> embed_chunks(chunks, batch_size=100)
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
    # If embedding_name provided, use it; otherwise get from embedder
    if embedding_name is None:
        embedding_name = embedder._generate_short_name()

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
    strategy: Optional[str] = None,
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
        strategy: Optional chunking strategy ("tokens" or "slide").
                 If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
        chunk_config: Optional chunking configuration (see chunking.chunk() for details)
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
        >>> from test_mcp.kb import Document
        >>> from test_mcp.kb.embedding import chunk_and_embed
        >>>
        >>> doc = Document.from_dict({...})
        >>> # Save document first
        >>> with get_db_session() as session:
        ...     session.add(doc)
        ...     session.commit()
        ...     # Chunk and embed (creates its own session if not provided)
        ...     chunks = chunk_and_embed(doc, embedding_name="openai-small")
    """
    from .chunking import chunk_document

    # Create session if not provided
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Chunk the document first
        chunks = chunk_document(
            document,
            strategy=strategy,
            config=chunk_config,
            session=session
        )

        # Then embed all the chunks
        if chunks:
            embed_chunks(
                chunks,
                embedding_name=embedding_name,
                provider=provider,
                model=model,
                batch_size=batch_size,
                session=session,
                **kwargs
            )

        # Commit if we created the session
        if own_session:
            session.commit()

        return chunks
    except Exception as e:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


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
    from .core import create_embedding_table

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
        >>> from test_mcp.kb.embedding import drop_embedding_table
        >>> result = drop_embedding_table("openai-small")
        >>> print(f"Dropped {result['count']} embeddings from {result['table_name']}")
    """
    from .core import get_embedding_table_name
    import logging

    logger = logging.getLogger(__name__)

    with get_db_session() as sess:
        config, embedding_table = _get_embedding_table(sess, embedding_name)
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
        >>> from test_mcp.kb.embedding import drop_embedding
        >>> # Drop from specific embedding table
        >>> count = drop_embedding("chunk-123", embedding_name="openai-small")
        >>> print(f"Deleted {count} embedding(s)")
        >>> # Drop from all embedding tables
        >>> count = drop_embedding("chunk-123")
        >>> print(f"Deleted {count} embedding(s) from all tables")
    """
    with get_db_session() as sess:
        total_deleted = 0
        inspector = sqlalchemy_inspect(sess.bind)

        if embedding_name is not None:
            # Delete from specific embedding table
            _, embedding_table = _get_embedding_table(sess, embedding_name)
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
                from .core import create_embedding_table
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
        >>> from test_mcp.kb.embedding import get_embeddings
        >>> # Get specific embedding
        >>> result = get_embeddings("chunk-123", embedding_name="openai-small")
        >>> # Returns: {"openai-small": {"id": "...", "embedding": [0.1, 0.2, ...]}}
        >>> # Get all embeddings
        >>> all_embeddings = get_embeddings("chunk-123")
        >>> # Returns: {"openai-small": {"id": "...", "embedding": [...]}, "st-minilm": {...}}
    """
    from .core import create_embedding_table

    with get_db_session() as sess:
        inspector = sqlalchemy_inspect(sess.bind)
        embeddings_dict = {}

        if embedding_name is not None:
            # Get specific embedding
            try:
                _, embedding_table = _get_embedding_table(sess, embedding_name)
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
        >>> from test_mcp.kb.embedding import get_embedding_vector
        >>> embedding = get_embedding_vector("chunk-123", embedding_name="openai-small")
        >>> # Returns: [0.1, 0.2, ...] or None
    """
    from .core import create_embedding_table

    with get_db_session() as sess:
        inspector = sqlalchemy_inspect(sess.bind)

        try:
            _, embedding_table = _get_embedding_table(sess, embedding_name)
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

