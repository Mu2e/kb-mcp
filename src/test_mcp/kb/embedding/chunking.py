"""Chunking utilities for embedding module."""

from typing import List, Optional, Dict, Any
from .core import Chunk, Document


def chunk_document(
    document: Document,
    strategy: Optional[str] = None,
    config: Optional[dict] = None,
    session=None,
) -> List[Chunk]:
    """
    Chunk a document and save chunks to the database.

    Args:
        document: Document object to chunk (must have text field)
        strategy: Optional chunking strategy ("tokens" or "slide").
                 If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
        config: Optional chunking configuration (see chunking.chunk() for details)
        session: Optional database session. If None, creates a new session.

    Returns:
        List of Chunk objects (saved to database)

    Raises:
        ValueError: If document has no text content

    Example:
        >>> from test_mcp.kb import Document
        >>> from test_mcp.kb.embedding import chunk_document
        >>>
        >>> doc = Document.from_dict({
        ...     "source_id": "test",
        ...     "doc_id": "123",
        ...     "text": "Long document text..."
        ... })
        >>> # Save document first
        >>> with get_db_session() as session:
        ...     session.add(doc)
        ...     session.commit()
        ...     chunks = chunk_document(doc, session=session)
    """
    import os
    from ...chunking import chunk
    from ..database import get_db_session
    from .core import ChunkStrategy

    if not document.text:
        raise ValueError("Document must have text content to chunk")

    if not document.id:
        raise ValueError("Document must be saved to database first (must have id)")

    # Get strategy from env var if not provided
    if strategy is None:
        strategy = os.getenv("CHUNK_STRATEGY", "tokens")

    # Chunk the text
    chunk_dicts = chunk(document.text, strategy=strategy, config=config)

    # Determine if we need to create a session
    own_session = session is None

    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get all strategy names from chunks
        strategy_names = {chunk_dict["chunk_strategy"] for chunk_dict in chunk_dicts}

        # Delete existing chunks for this document with any of these strategies
        # This ensures re-chunking with the same strategy replaces old chunks
        if strategy_names:
            deleted_count = session.query(Chunk).filter(
                Chunk.document_id == document.id,
                Chunk.chunk_strategy.in_(strategy_names)
            ).delete(synchronize_session=False)

            if deleted_count > 0:
                strategy_list = ", ".join(sorted(strategy_names))
                print(f"Replacing {deleted_count} existing chunk(s) for document {document.id[:8]}... (strategies: {strategy_list})")
                session.flush()  # Ensure deletions are committed before inserts

        # Ensure ChunkStrategy records exist for all chunk strategies
        for strategy_name in strategy_names:
            # Check if strategy already exists
            existing_strategy = session.query(ChunkStrategy).filter(
                ChunkStrategy.strategy == strategy_name
            ).first()

            if not existing_strategy:
                # Create new ChunkStrategy record
                # Get meta from first chunk with this strategy
                chunk_meta = next(
                    (cd["meta"] for cd in chunk_dicts if cd["chunk_strategy"] == strategy_name),
                    {}
                )
                new_strategy = ChunkStrategy(
                    strategy=strategy_name,
                    meta=chunk_meta,
                )
                session.add(new_strategy)

        # Flush to ensure strategies are in database before creating chunks
        session.flush()

        # Create Chunk objects from dictionaries
        chunks = []
        for chunk_dict in chunk_dicts:
            # Merge chunk_dict with document_id (exclude meta since it's in ChunkStrategy now)
            chunk_data = {
                "document_id": document.id,
                "text": chunk_dict["text"],
                "chunk_index": chunk_dict["chunk_index"],
                "char_start_index": chunk_dict.get("char_start_index"),
                "char_end_index": chunk_dict.get("char_end_index"),
                "token_length": chunk_dict.get("token_length"),
                "chunk_strategy": chunk_dict.get("chunk_strategy"),
            }
            chunk_obj = Chunk.from_dict(chunk_data)
            chunks.append(chunk_obj)

        # Save chunks to database
        _save_chunks_to_session(chunks, session, commit=own_session)

        # If we own the session, convert to dicts and close
        if own_session:
            result = [chunk.to_dict() for chunk in chunks]
            session.commit()
            session.close()
            return [Chunk.from_dict(cd) for cd in result]
        else:
            return chunks

    except Exception:
        if own_session:
            session.rollback()
            session.close()
        raise


def _save_chunks_to_session(chunks: List[Chunk], session, commit: bool = False) -> None:
    """Helper to save chunks to session and detach them."""
    for chunk_obj in chunks:
        session.add(chunk_obj)
    session.flush()  # Flush to get IDs

    # Refresh to ensure all attributes are loaded
    #for chunk_obj in chunks:
    #    session.refresh(chunk_obj)

    if commit:
        session.commit()

    # Detach chunks from session so they can be used outside
    #for chunk_obj in chunks:
    #    session.expunge(chunk_obj)


def get_chunk_strategies(session=None) -> List[Dict[str, Any]]:
    """Get all chunking strategies used in the database.

    Args:
        session: Optional database session. If None, creates a new session.

    Returns:
        List of dictionaries with:
        - strategy: str - The chunking strategy identifier
        - meta: dict - Strategy configuration parameters
        - count: int - Number of chunks using this strategy
        - created_time: str - ISO format timestamp
    """
    from .core import Chunk, ChunkStrategy
    from ..database import get_db_session
    from sqlalchemy import func

    def _query(sess):
        # Join ChunkStrategy with Chunk counts
        results = sess.query(
            ChunkStrategy.strategy,
            ChunkStrategy.meta,
            ChunkStrategy.created_time,
            func.count(Chunk.id).label('count')
        ).outerjoin(
            Chunk, ChunkStrategy.strategy == Chunk.chunk_strategy
        ).group_by(
            ChunkStrategy.strategy,
            ChunkStrategy.meta,
            ChunkStrategy.created_time
        ).order_by(ChunkStrategy.strategy).all()

        return [
            {
                "strategy": strategy,
                "meta": meta if meta else {},
                "count": count,
                "created_time": created_time.isoformat() if created_time else None,
            }
            for strategy, meta, created_time, count in results
        ]

    if session is not None:
        return _query(session)
    else:
        with get_db_session() as sess:
            return _query(sess)


def get_chunks(
    document_id: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    session=None,
):
    """Get chunks, optionally filtered by document and/or strategy.

    Args:
        document_id: Optional UUID of the document. If None, returns all chunks.
        chunk_strategy: Optional filter for specific chunking strategy
        limit: Optional limit on number of chunks returned (for pagination)
        offset: Optional offset for pagination (number of chunks to skip)
        session: Optional database session. If None, creates a new session.

    Returns:
        If session is provided: List of Chunk objects (attached to session)
        If session is None: List of chunk dictionaries (from Chunk.to_dict())

    Examples:
        >>> # Get all chunks for a document
        >>> chunks = get_chunks(document_id="abc-123")

        >>> # Get all chunks across all documents
        >>> all_chunks = get_chunks()

        >>> # Get first 100 chunks with pagination
        >>> page1 = get_chunks(limit=100, offset=0)
        >>> page2 = get_chunks(limit=100, offset=100)

        >>> # Get chunks with specific strategy across all documents
        >>> token_chunks = get_chunks(chunk_strategy="tokens_1000_200")
    """
    from .core import Chunk
    from ..database import get_db_session

    def _query(sess, return_dicts=False):
        # Build query
        query = sess.query(Chunk)

        # Apply document filter if provided
        if document_id is not None:
            query = query.filter(Chunk.document_id == document_id)

        # Apply strategy filter if provided
        if chunk_strategy:
            query = query.filter(Chunk.chunk_strategy == chunk_strategy)

        # Order by document_id and chunk_index for consistent pagination
        if document_id is not None:
            # When filtering by document, order by chunk_index only
            query = query.order_by(Chunk.chunk_index)
        else:
            # When querying all chunks, order by document_id then chunk_index
            query = query.order_by(Chunk.document_id, Chunk.chunk_index)

        # Apply pagination if specified
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        chunks = query.all()

        if return_dicts:
            # Convert to dictionaries while session is still open
            return [chunk.to_dict() for chunk in chunks]
        else:
            # Return actual Chunk objects
            return chunks

    if session is not None:
        # Return Chunk objects when session is provided
        return _query(session, return_dicts=False)
    else:
        # Return dictionaries when no session provided
        with get_db_session() as sess:
            return _query(sess, return_dicts=True)


def drop_chunks(
    document_id: str,
    chunk_strategy: Optional[str] = None,
    session=None,
) -> int:
    """Drop chunks for a specific document.

    Args:
        document_id: UUID of the document
        chunk_strategy: Optional filter for specific chunking strategy.
                       If provided, only drops chunks with this strategy.
                       If None, drops ALL chunks for the document.
        session: Optional database session. If None, creates a new session.

    Returns:
        Number of chunks deleted

    Example:
        >>> # Drop all chunks for a document
        >>> count = drop_chunks(document_id="abc-123")
        >>> print(f"Deleted {count} chunks")

        >>> # Drop only chunks with specific strategy
        >>> count = drop_chunks(document_id="abc-123", chunk_strategy="tokens_1000_200")
        >>> print(f"Deleted {count} chunks with strategy tokens_1000_200")
    """
    from .core import Chunk
    from ..database import get_db_session

    def _delete(sess):
        # Build query
        query = sess.query(Chunk).filter(Chunk.document_id == document_id)

        # Apply strategy filter if provided
        if chunk_strategy:
            query = query.filter(Chunk.chunk_strategy == chunk_strategy)

        # Delete and return count
        deleted_count = query.delete(synchronize_session=False)
        sess.flush()
        return deleted_count

    if session is not None:
        # Use provided session (don't commit)
        return _delete(session)
    else:
        # Create own session and commit
        with get_db_session() as sess:
            count = _delete(sess)
            sess.commit()
            return count
