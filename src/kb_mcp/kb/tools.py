"""Tools for batch operations on the knowledge base."""

import logging
from typing import Dict, Any, Optional

from .database import get_db_session
from .db_models import Document
from .embedding.db_models import Chunk, get_embedding_table
from ..chunking.chunking import get_chunk_strategy_suffix

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
    chunk_config: Optional[Dict[str, Any]] = None,
    include_images: bool = True,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Chunk and embed all documents for a source_id that don't have chunks yet.

    Find documents without chunks, then chunks and embeds them.
    Assumes that if chunks exist, they are already embedded.

    Args:
        source_id: Source identifier to process documents for
        chunk_strategy: Optional chunking strategy ("tokens" or "slide" or "summary"). If None, uses default.
        chunk_config: Optional chunking configuration dict. Supports:
                     - prepend_gist: If False, don't prepend gist (default: True)
                     - prepend_section_path: If False, don't prepend section path (default: True)
                     - chunk_size: Token chunk size (for tokens/slide strategies)
                     - chunk_overlap: Token overlap (for tokens/slide strategies)
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
        from kb_mcp.kb.tools import chunk_and_embed_all
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
        # Build query based on chunk strategy
        # For "summary" strategy, find documents with summaries but without summary chunks
        # For other strategies, find documents without any chunks
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.text.isnot(None),
            Document.text != "",
            Document.doc_type != "image"
        )
        
        # Determine the actual strategy name that will be created (including suffix from chunk_config)
        actual_strategy = chunk_strategy
        if chunk_config and chunk_strategy and chunk_strategy not in ["summary", "image"]:
            prepend_gist = chunk_config.get("prepend_gist", True)
            prepend_section_path = chunk_config.get("prepend_section_path", True)
            suffix = get_chunk_strategy_suffix(
                prepend_gist=prepend_gist,
                prepend_section_path=prepend_section_path
            )
            actual_strategy = chunk_strategy + suffix if suffix else chunk_strategy
        
        if chunk_strategy == "summary":
            # For summary strategy: find documents with summaries (non-empty) but without summary chunks
            from sqlalchemy import and_
            query = query.filter(
                Document.summary.isnot(None),
                Document.summary != ""
            )
            # LEFT JOIN to find documents without summary chunks
            # Use subquery to check for existing summary chunks
            query = query.outerjoin(
                Chunk, 
                and_(
                    Document.id == Chunk.document_id,
                    Chunk.chunk_strategy == "summary"
                )
            ).filter(Chunk.id.is_(None))
        else:
            # For other strategies: find documents without chunks of this specific strategy
            # This allows creating multiple strategies for the same documents (e.g., tokens and tokens_no_gist)
            from sqlalchemy import and_
            query = query.outerjoin(
                Chunk,
                and_(
                    Document.id == Chunk.document_id,
                    Chunk.chunk_strategy == actual_strategy
                )
            ).filter(Chunk.id.is_(None))
        
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
                    chunk_config=chunk_config,
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
        from kb_mcp.kb.tools import image_chunk_and_embed_all
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


def embed_all(
    source_id: Optional[str] = None,
    chunk_strategy: Optional[str] = None,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate embeddings for all chunks that don't have them yet.

    Finds chunks (optionally filtered by source_id and/or chunk_strategy) that don't have
    embeddings for the specified embedding config, and generates embeddings for them.

    This is useful for:

    - Embedding chunks that were created without embeddings (e.g. from a new importer)
    - Re-embedding existing chunks with a different embedding model
    

    Args:
        source_id: Optional source identifier to filter chunks. If None, processes all sources.
        chunk_strategy: Optional chunking strategy to filter ("tokens", "slide", "summary", "image").
                       If None, processes chunks from all strategies.
        embedding_name: Optional short name for embedding config (e.g., "openai-small").
                       If provided, overrides provider/model.
        provider: Optional embedding provider (e.g., "openai", "sentence-transformers")
        model: Optional embedding model (e.g., "text-embedding-3-small")

    Returns:
        Dictionary with:
        - total_chunks: Total number of chunks found matching filters
        - embedded: Number of chunks that were embedded
        - errors: Number of chunks that failed

    Example:
        ```python
        from kb_mcp.kb.tools import embed_all

        # Embed all chunks from a source with default embedding model
        result = embed_all(source_id="inspire-hep")

        # Embed all summary chunks across all sources with a specific model
        result = embed_all(chunk_strategy="summary", provider="openai", model="text-embedding-3-large")

        # Re-embed everything with a different embedding model
        result = embed_all(provider="sentence-transformers", model="all-MiniLM-L6-v2")

        print(f"Embedded {result['embedded']} of {result['total_chunks']} chunks")
        ```
    """
    if not EMBEDDING_AVAILABLE:
        raise ImportError(
            "Embedding module not available. Install with: pip install -e '.[embedding]'"
        )

    from .embedding import embed_chunks

    logger.info(
        f"Starting embed_all - source_id: {source_id or 'all'}, "
        f"chunk_strategy: {chunk_strategy or 'all'}"
    )

    with get_db_session() as session:
        # Build base query to find chunks
        query = session.query(Chunk)

        # Filter by source_id if provided
        if source_id:
            query = query.join(Document).filter(Document.source_id == source_id)

        # Filter by chunk_strategy if provided
        if chunk_strategy:
            query = query.filter(Chunk.chunk_strategy == chunk_strategy)

        # Get embedding table if config exists
        embedding_table = get_embedding_table(
            session,
            embedding_name=embedding_name,
            provider=provider,
            model=model
        )

        if embedding_table is not None:
            # Do LEFT JOIN to find chunks without embeddings
            query = query.outerjoin(
                embedding_table,
                Chunk.id == embedding_table.c.chunk_id
            ).filter(embedding_table.c.chunk_id.is_(None))
        else:
            # Config doesn't exist - all chunks need embedding
            logger.info(
                "Embedding config doesn't exist yet - all matching chunks will be embedded"
            )

        # Get all chunks that need embedding
        chunks = query.all()

        if not chunks:
            logger.info(
                f"No chunks found that need embedding - source_id: {source_id or 'all'}, "
                f"chunk_strategy: {chunk_strategy or 'all'}"
            )
            return {
                "total_chunks": 0,
                "embedded": 0,
                "errors": 0,
            }

        total_chunks = len(chunks)
        logger.info(f"Found {total_chunks} chunk(s) to embed")

        # Group chunks by document
        from collections import defaultdict
        chunks_by_doc = defaultdict(list)
        for chunk in chunks:
            chunks_by_doc[chunk.document_id].append(chunk)

        num_documents = len(chunks_by_doc)
        logger.info(f"Chunks belong to {num_documents} document(s)")

        # Embed chunks document by document with progress bar
        # This allows us to create proper logs per document
        from tqdm import tqdm
        import time
        import socket

        embedded = 0
        errors = 0

        # Progress bar shows chunks, not documents
        with tqdm(total=total_chunks, desc="Embedding chunks", unit="chunk") as pbar:
            for doc_id, doc_chunks in chunks_by_doc.items():
                try:
                    from .embedding.db_models import ChunkEmbeddingLog

                    embed_start = time.time()
                    embed_chunks(
                        doc_chunks,
                        embedding_name=embedding_name,
                        provider=provider,
                        model=model,
                        session=session,
                    )
                    embed_time = time.time() - embed_start

                    # Create log entry for this document
                    # Get embedding config name for logging
                    from .embedding.utils import get_embedder
                    embedder = get_embedder(
                        embedding_name=embedding_name,
                        provider=provider,
                        model=model,
                        session=session
                    )
                    config_name = embedder._generate_short_name()

                    hostname = socket.gethostname()
                    log_entry = ChunkEmbeddingLog(
                        document_id=doc_id,
                        chunking_time_seconds=0.0,  # Only embedding, not chunking
                        embedding_time_seconds=round(embed_time, 3),
                        total_time_seconds=round(embed_time, 3),
                        num_chunks=len(doc_chunks),
                        num_embeddings=len(doc_chunks),
                        chunk_strategy=doc_chunks[0].chunk_strategy if doc_chunks else chunk_strategy,
                        embedding_name=config_name,
                        hostname=hostname,
                    )
                    session.add(log_entry)
                    session.commit()

                    embedded += len(doc_chunks)
                    pbar.update(len(doc_chunks))
                except Exception as e:
                    errors += len(doc_chunks)
                    logger.error(f"Error embedding document {doc_id}: {e}")
                    pbar.update(len(doc_chunks))

        logger.info(f"Successfully embedded {embedded} chunks from {num_documents} documents")

    result = {
        "total_chunks": total_chunks,
        "embedded": embedded,
        "errors": errors,
    }

    logger.info(
        f"Completed embed_all - Embedded: {embedded}, Errors: {errors} "
        f"(out of {total_chunks} chunks)"
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
        from kb_mcp.kb.tools import summarize_all
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
        from sqlalchemy import or_
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.text.isnot(None),
            Document.text != "",
            or_(
                Document.summary.is_(None),
                Document.summary == ""
            ),  # No summary yet (None or empty string)
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

