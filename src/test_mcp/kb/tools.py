"""Tools for batch operations on the knowledge base."""

import logging
from typing import Dict, Any, Optional

from .database import get_db_session
from .db_models import Document
from .embedding.db_models import Chunk

logger = logging.getLogger(__name__)

# Check if embedding module is available
try:
    from .embedding import chunk_and_embed  # noqa: F401
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False


def chunk_and_embed_all(
    source_id: str,
    chunk_strategy: Optional[str] = None,
    include_images: bool = True,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Chunk and embed all documents for a source_id that don't have chunks yet.

    Uses SQL JOINs to efficiently find documents without chunks, then chunks and embeds them.
    Assumes that if chunks exist, they are already embedded.

    Args:
        source_id: Source identifier to process documents for
        chunk_strategy: Optional chunking strategy ("tokens" or "slide"). If None, uses default.
        include_images: If True, also process image documents using "image" strategy (default: True)
        embedding_name: Optional short name for embedding config (e.g., "openai-small").
                       If provided, overrides provider/model.
        provider: Optional embedding provider (e.g., "openai", "voyage")
        model: Optional embedding model (e.g., "text-embedding-3-small")

    Returns:
        Dictionary with:
        - processed: Number of text documents processed
        - chunked: Number of text documents that were chunked and embedded
        - skipped: Number of text documents skipped (no text or already had chunks)
        - errors: Number of text documents that failed
        - image_processed: Number of image documents processed (if include_images=True)
        - image_chunked: Number of image documents chunked (if include_images=True)
        - image_skipped: Number of image documents skipped (if include_images=True)
        - image_errors: Number of image documents that failed (if include_images=True)

    Example:
        ```python
        from test_mcp.kb.tools import chunk_and_embed_all
        result = chunk_and_embed_all("inspire-hep")
        print(f"Processed {result['processed']} documents, chunked {result['chunked']}")
        print(f"Processed {result['image_processed']} images, chunked {result['image_chunked']}")
        ```
    """
    if not EMBEDDING_AVAILABLE:
        raise ImportError(
            "Embedding module not available. Install with: pip install -e '.[embedding]'"
        )
    
    logger.info(f"Starting chunk_and_embed_all for source_id: {source_id}")
    
    with get_db_session() as session:
        # Find documents without chunks using LEFT JOIN
        # Exclude image documents - they should use image_chunk_and_embed_all() instead
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.text.isnot(None),
            Document.text != "",
            Document.doc_type != "image"
        ).outerjoin(Chunk, Document.id == Chunk.document_id).filter(Chunk.id.is_(None))
        
        documents = query.all()
        
        if not documents:
            logger.info(f"No documents found for source_id: {source_id} that need chunking")
            return {
                "processed": 0,
                "chunked": 0,
                "skipped": 0,
                "errors": 0,
            }
        
        logger.info(f"Found {len(documents)} document(s) for source_id: {source_id} that need chunking")
        
        processed = 0
        chunked = 0
        skipped = 0
        errors = 0
        
        for doc in documents:
            try:
                processed += 1

                # Chunk and embed the document using the document method
                logger.info(f"Chunking and embedding document {doc.id} ({doc.doc_id or doc.id})")
                chunks = doc.chunk_and_embed(
                    chunk_strategy=chunk_strategy,
                    embedding_name=embedding_name,
                    provider=provider,
                    model=model,
                )

                if chunks:
                    chunked += 1
                    logger.info(f"Successfully chunked and embedded document {doc.id} ({len(chunks)} chunks)")
                else:
                    skipped += 1
                    logger.warning(f"No chunks created for document {doc.id}")

            except Exception as e:
                errors += 1
                logger.error(f"Error processing document {doc.id}: {e}", exc_info=True)
                continue

    result = {
        "processed": processed,
        "chunked": chunked,
        "skipped": skipped,
        "errors": errors,
    }

    # Optionally process image documents
    if include_images:
        logger.info(f"Processing image documents for source_id: {source_id}")
        image_result = image_chunk_and_embed_all(
            source_id=source_id,
            embedding_name=embedding_name,
            provider=provider,
            model=model,
            session=session,
        )
        result["image_processed"] = image_result["processed"]
        result["image_chunked"] = image_result["chunked"]
        result["image_skipped"] = image_result["skipped"]
        result["image_errors"] = image_result["errors"]

    # Update log message to include images if processed
    log_msg = (
        f"Completed chunk_and_embed_all for source_id: {source_id}. "
        f"Text docs - Processed: {processed}, Chunked: {chunked}, Skipped: {skipped}, Errors: {errors}"
    )
    if include_images and "image_processed" in result:
        log_msg += (
            f". Image docs - Processed: {result['image_processed']}, "
            f"Chunked: {result['image_chunked']}, Skipped: {result['image_skipped']}, "
            f"Errors: {result['image_errors']}"
        )
    logger.info(log_msg)

    return result


def image_chunk_and_embed_all(
    source_id: str,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """Chunk and embed all image documents for a source_id that don't have chunks yet.

    Uses the special "image" chunking strategy that creates a single chunk per image
    with the image description, optionally prepended with the parent document's gist.

    Args:
        source_id: Source identifier to process image documents for
        embedding_name: Optional short name for embedding config (e.g., "openai-small").
                       If provided, overrides provider/model.
        provider: Optional embedding provider (e.g., "openai", "voyage")
        model: Optional embedding model (e.g., "text-embedding-3-small")
        session: Optional database session. If None, creates a new session.

    Returns:
        Dictionary with:
        - processed: Number of image documents processed
        - chunked: Number of image documents that were chunked and embedded
        - skipped: Number of image documents skipped (no text or already had chunks)
        - errors: Number of image documents that failed

    Example:
        ```python
        from test_mcp.kb.tools import image_chunk_and_embed_all
        result = image_chunk_and_embed_all("inspire-hep")
        print(f"Processed {result['processed']} images, chunked {result['chunked']}")
        ```
    """
    if not EMBEDDING_AVAILABLE:
        raise ImportError(
            "Embedding module not available. Install with: pip install -e '.[embedding]'"
        )

    logger.info(f"Starting image_chunk_and_embed_all for source_id: {source_id}")

    with get_db_session(session) as session:
        # Find image documents without chunks using LEFT JOIN
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.doc_type == "image",
            Document.text.isnot(None),
            Document.text != ""
        ).outerjoin(Chunk, Document.id == Chunk.document_id).filter(Chunk.id.is_(None))

        documents = query.all()

        if not documents:
            logger.info(f"No image documents found for source_id: {source_id} that need chunking")
            return {
                "processed": 0,
                "chunked": 0,
                "skipped": 0,
                "errors": 0,
            }

        logger.info(f"Found {len(documents)} image document(s) for source_id: {source_id} that need chunking")

        processed = 0
        chunked = 0
        skipped = 0
        errors = 0

        for doc in documents:
            try:
                processed += 1

                # Chunk and embed the image document using "image" strategy
                logger.info(f"Chunking and embedding image document {doc.id} ({doc.doc_id or doc.id})")
                chunks = doc.chunk_and_embed(
                    chunk_strategy="image",
                    embedding_name=embedding_name,
                    provider=provider,
                    model=model,
                )

                if chunks:
                    chunked += 1
                    logger.info(f"Successfully chunked and embedded image document {doc.id}")
                else:
                    skipped += 1
                    logger.warning(f"No chunks created for image document {doc.id}")

            except Exception as e:
                errors += 1
                logger.error(f"Error processing image document {doc.id}: {e}", exc_info=True)
                continue

        result = {
            "processed": processed,
            "chunked": chunked,
            "skipped": skipped,
            "errors": errors,
        }

        logger.info(
            f"Completed image_chunk_and_embed_all for source_id: {source_id}. "
            f"Processed: {processed}, Chunked: {chunked}, Skipped: {skipped}, Errors: {errors}"
        )

        return result


def summarize_all(
    source_id: str,
    model: Optional[str] = None,
    create_summary_chunk: bool = True,
    embed_summary_chunk: bool = False,
    embedding_name: Optional[str] = None,
    embedding_provider: Optional[str] = None,
    embedding_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate summaries for all documents from a source that don't have them yet.

    Finds documents without summaries and generates title, gist, and summary.
    Optionally creates a summary chunk and embeds it.

    Args:
        source_id: Source identifier to process documents for
        model: Optional model name for summary generation (overrides SUMMARY_MODEL env var)
        create_summary_chunk: Whether to create a chunk with strategy="summary" (default: True)
        embed_summary_chunk: Whether to embed the summary chunk (requires create_summary_chunk=True, default: False)
        embedding_name: Optional embedding name for summary chunk (used if embed_summary_chunk=True)
        embedding_provider: Optional embedding provider (used if embed_summary_chunk=True and embedding_name not provided)
        embedding_model: Optional embedding model (used if embed_summary_chunk=True and embedding_name not provided)

    Returns:
        Dictionary with:
        - processed: Number of documents processed
        - summarized: Number of documents that got summaries
        - chunked: Number of summary chunks created (if create_summary_chunk=True)
        - embedded: Number of summary chunks embedded (if embed_summary_chunk=True)
        - skipped: Number of documents skipped (no text)
        - errors: Number of documents that failed

    Example:
        ```python
        from test_mcp.kb.tools import summarize_all
        # Generate summaries and create chunks (but don't embed yet)
        result = summarize_all("inspire-hep", create_summary_chunk=True)
        print(f"Summarized {result['summarized']} documents, created {result['chunked']} chunks")

        # Generate summaries and create+embed chunks
        result = summarize_all("inspire-hep", create_summary_chunk=True, embed_summary_chunk=True)
        ```
    """
    # Summary module availability is checked by doc.generate_summary()

    if embed_summary_chunk and not EMBEDDING_AVAILABLE:
        raise ImportError(
            "Embedding module not available. Install with: pip install -e '.[embedding]'"
        )

    logger.info(f"Starting summarize_all for source_id: {source_id}")

    with get_db_session() as session:
        # Find documents without summaries
        # Exclude image documents - they already have descriptions in their text field
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.text.isnot(None),
            Document.text != "",
            Document.summary.is_(None),  # No summary yet
            Document.doc_type != "image"
        )

        documents = query.all()

        if not documents:
            logger.info(f"No documents found for source_id: {source_id} that need summarization")
            return {
                "processed": 0,
                "summarized": 0,
                "chunked": 0,
                "embedded": 0,
                "skipped": 0,
                "errors": 0,
            }

        logger.info(f"Found {len(documents)} document(s) for source_id: {source_id} that need summarization")

        processed = 0
        summarized = 0
        chunked = 0
        embedded = 0
        skipped = 0
        errors = 0

        for doc in documents:
            try:
                processed += 1

                # Generate summary using the document method
                logger.info(f"Generating summary for document {doc.id} ({doc.doc_id or doc.id})")
                doc.generate_summary(
                    include_title=True,
                    include_gist=True,
                    include_summary=True,
                    model=model,
                )

                summarized += 1
                logger.info(f"Successfully generated summary for document {doc.id}")

                # Optionally create summary chunk
                if create_summary_chunk and doc.summary:
                    logger.info(f"Creating summary chunk for document {doc.id}")

                    if embed_summary_chunk:
                        # Create and embed the summary chunk using the document method
                        chunks = doc.chunk_and_embed(
                            chunk_strategy="summary",
                            embedding_name=embedding_name,
                            provider=embedding_provider,
                            model=embedding_model,
                        )
                        if chunks:
                            chunked += 1
                            embedded += 1
                            logger.info(f"Created and embedded summary chunk for document {doc.id}")
                    else:
                        # Just create the chunk without embedding
                        chunks = doc.chunk(chunk_strategy="summary")
                        if chunks:
                            chunked += 1
                            logger.info(f"Created summary chunk for document {doc.id}")

            except Exception as e:
                errors += 1
                logger.error(f"Error processing document {doc.id}: {e}", exc_info=True)
                session.rollback()
                continue

    result = {
        "processed": processed,
        "summarized": summarized,
        "chunked": chunked,
        "embedded": embedded,
        "skipped": skipped,
        "errors": errors,
    }

    logger.info(
        f"Completed summarize_all for source_id: {source_id}. "
        f"Processed: {processed}, Summarized: {summarized}, "
        f"Chunked: {chunked}, Embedded: {embedded}, Errors: {errors}"
    )

    return result

