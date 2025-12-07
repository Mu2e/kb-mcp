"""Chunking utilities for embedding module."""

import logging
from typing import List, Optional, Dict, Any
from .core import Chunk, Document

logger = logging.getLogger(__name__)


def chunk_document(
    document: Document,
    chunk_strategy: Optional[str] = None,
    config: Optional[dict] = None,
    session=None,
) -> List[Chunk]:
    """
    Chunk a document and save chunks to the database.

    Args:
        document: Document object to chunk (must have text field)
        chunk_strategy: Optional chunking strategy ("tokens", "slide", or "summary").
                       If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
                       "summary" creates a single chunk from document.summary field.
        config: Optional chunking configuration. Supports embedding context flags:
                - prepend_section_path: If True, prepend section_path before embedding (default: True)
                - prepend_gist: If True, prepend document gist before embedding (default: True)
                - Other strategy-specific parameters (see chunking.chunk() for details)
        session: Optional database session. If None, creates a new session.

    Returns:
        List of Chunk objects (saved to database)

    Raises:
        ValueError: If document has no text content or if strategy="summary" and document has no summary

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
    if chunk_strategy is None:
        chunk_strategy = os.getenv("CHUNK_STRATEGY", "tokens")

    # Extract embedding context flags from config (default to True)
    if config is None:
        config = {}
    prepend_section_path = config.get("prepend_section_path", True)
    prepend_gist = config.get("prepend_gist", True)

    # Handle "summary" strategy specially - create single chunk from document summary
    if chunk_strategy == "summary":
        if not document.summary:
            raise ValueError("Document must have summary to use 'summary' chunking strategy. Call generate_summary() first.")

        # Calculate token length for the summary
        from ...chunking import count_tokens
        summary_tokens = count_tokens(document.summary)

        # Create a single chunk dict with the summary
        chunk_dicts = [{
            "text": document.summary,
            "chunk_index": 0,
            "char_start_index": None,
            "char_end_index": None,
            "token_length": summary_tokens,
            "chunk_strategy": "summary",
            "meta": {"source": "document.summary"},
        }]
    elif chunk_strategy == "image":
        # Handle "image" strategy - create single chunk from image description
        # Optionally prepend parent document's gist for context
        if document.doc_type != "image":
            raise ValueError("'image' chunking strategy can only be used with image documents (doc_type='image').")

        if not document.text:
            raise ValueError("Image document must have text (description) to use 'image' chunking strategy.")

        from ...chunking import count_tokens

        # Build chunk text with optional parent gist prepending
        text_parts = []

        # Prepend parent's gist if enabled and available
        if prepend_gist and document.parent_id:
            from ..database import get_db_session
            # Use existing session if available, otherwise create temporary one
            temp_session = session if session else get_db_session().__enter__()
            try:
                parent = temp_session.query(Document).filter(Document.id == document.parent_id).first()
                if parent and parent.gist:
                    text_parts.append(f"Document context: {parent.gist}")
            finally:
                # Only close if we created a temporary session
                if session is None:
                    temp_session.close()

        # Add image description
        text_parts.append(document.text)

        chunk_text = "\n\n".join(text_parts)
        image_tokens = count_tokens(chunk_text)

        # Create a single chunk dict with the image description
        chunk_dicts = [{
            "text": chunk_text,
            "chunk_index": 0,
            "char_start_index": None,
            "char_end_index": None,
            "token_length": image_tokens,
            "chunk_strategy": "image",
            "meta": {"source": "image_description"},
        }]
    else:
        # Regular chunking
        chunk_dicts = chunk(document.text, strategy=chunk_strategy, config=config)

        # Update chunk_strategy names to include prepending configuration
        # This ensures different prepending settings create separate strategy records
        if (not prepend_section_path) or (not prepend_gist):
            if (not prepend_section_path) and (not prepend_gist):
                suffix = "_no_context"
            elif not prepend_section_path:
                suffix = "_no_section"
            else:  # not prepend_gist
                suffix = "_no_gist"
            print(suffix)

            for chunk_dict in chunk_dicts:
                chunk_dict["chunk_strategy"] = chunk_dict["chunk_strategy"] + suffix


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
                logger.info(f"Replacing {deleted_count} existing chunk(s) for document {document.id[:8]}... (strategies: {strategy_list})")
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
                # Add embedding context flags to meta
                chunk_meta["prepend_section_path"] = prepend_section_path
                chunk_meta["prepend_gist"] = prepend_gist

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
            # Build chunk text with optional context prepending
            # Skip prepending for summary and image strategies (they handle their own context)
            text_parts = []
            context_added = False
            is_special_strategy = chunk_dict.get("chunk_strategy") in ["summary", "image"]

            # Prepend section_path if enabled and available (not for special strategies)
            if not is_special_strategy and prepend_section_path and chunk_dict.get("section_path"):
                text_parts.append(f"Section: {chunk_dict['section_path']}")
                context_added = True

            # Prepend gist if enabled and available (not for special strategies)
            if not is_special_strategy and prepend_gist and document.gist:
                text_parts.append(f"Context: {document.gist}")
                context_added = True

            # Add the chunk text itself
            text_parts.append(chunk_dict["text"])

            # Join with double newlines for clear separation
            final_text = "\n\n".join(text_parts)

            # Only recalculate token length if we added context
            if context_added:
                from ...chunking import count_tokens
                final_token_length = count_tokens(final_text)
            else:
                final_token_length = chunk_dict.get("token_length")

            # Merge chunk_dict with document_id (exclude meta since it's in ChunkStrategy now)
            chunk_data = {
                "document_id": document.id,
                "text": final_text,
                "chunk_index": chunk_dict["chunk_index"],
                "char_start_index": chunk_dict.get("char_start_index"),
                "char_end_index": chunk_dict.get("char_end_index"),
                "token_length": final_token_length,
                "section_path": chunk_dict.get("section_path"),  # Store original section_path
                "chunk_strategy": chunk_dict.get("chunk_strategy"),
            }
            chunk_obj = Chunk.from_dict(chunk_data)
            chunks.append(chunk_obj)

        # Save chunks to database
        _save_chunks_to_session(chunks, session, commit=False)

        # Commit if we own the session
        if own_session:
            session.commit()
            # Refresh and expunge chunks so they can be used after session closes
            for chunk in chunks:
                session.refresh(chunk)
                session.expunge(chunk)

        return chunks
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        # Close session if we created it
        if own_session and session:
            session.close()


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


def get_chunk_strategies(document_id: Optional[str] = None, session=None) -> List[Dict[str, Any]]:
    """Get chunking strategies, optionally filtered by document.

    Args:
        document_id: Optional UUID of the document. If None, returns all strategies.
        session: Optional database session. If None, creates a new session.

    Returns:
        List of dictionaries with:
        - strategy: str - The chunking strategy identifier
        - meta: dict - Strategy configuration parameters
        - count: int - Number of chunks using this strategy (for the document if document_id provided)
        - created_time: str - ISO format timestamp
    """
    from .core import Chunk, ChunkStrategy
    from ..database import get_db_session
    from sqlalchemy import func

    def _query(sess):
        # Build base query
        query = sess.query(
            ChunkStrategy.strategy,
            ChunkStrategy.meta,
            ChunkStrategy.created_time,
            func.count(Chunk.id).label('count')
        ).outerjoin(
            Chunk, ChunkStrategy.strategy == Chunk.chunk_strategy
        )
        
        # Filter by document if provided
        if document_id is not None:
            query = query.filter(Chunk.document_id == document_id)
        
        # Group and order
        results = query.group_by(
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
