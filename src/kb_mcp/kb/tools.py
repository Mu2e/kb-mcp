"""Tools for batch operations on the knowledge base."""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from tqdm import tqdm

from .database import get_db_session
from .db_models import Document, PrivacyFilter
from .embedding.db_models import Chunk, get_embedding_table
from ..chunking.chunking import get_strategy_name
from ..config import get_embedding_config

logger = logging.getLogger(__name__)


def ingest(
    file_path: Union[str, Path],
    *,
    source_id: str,
    doc_id: str,
    parser_name: Optional[str] = None,
    extract_images: Optional[bool] = None,
    describe_images: Optional[bool] = None,
    dedup_level: Optional[int] = None,
    force_reparse: bool = False,
    copy_to_kb: bool = False,
    uri: Optional[str] = None,
    meta: Optional[Dict] = None,
    generate_summary: bool = True,
    summary_include_metadata: bool = False,
    chunk_and_embed: bool = True,
    create_summary_chunks: bool = True,
    chunk_strategy: Optional[str] = None,
    chunk_config: Optional[Dict[str, Any]] = None,
    embedding_name: Optional[str] = None,
    embedding_provider: Optional[str] = None,
    embedding_model: Optional[str] = None,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """High-level function to ingest a document with full processing workflow.

    This is the top-level wrapper that executes the complete document ingestion pipeline:
    1. Add document (parse file and create Document records)
    2. Generate summaries (default: True)
    3. Chunk and embed (default: True)

    For lower-level control, use add_document() directly.

    Args:
        file_path: Path to the document file
        source_id: Source identifier
        doc_id: Document ID within the source
        parser_name: Parser to use (default: "kb-mcp")
        extract_images: If True, create separate Document objects for extracted images
        describe_images: If True, generate LLM descriptions for images
        dedup_level: Deduplication level (0-4). See add_document() for details.
        force_reparse: If True, re-parse even if document already exists
        copy_to_kb: If True, copy file to data/sources/{source_id}/ directory
        uri: Optional URI for the document
        meta: Optional metadata dictionary
        generate_summary: If True, generate title, gist, and summary for documents (default: True)
        chunk_and_embed: If True, chunk and embed the documents (default: True)
        create_summary_chunks: If True, create summary chunks when summary is generated (default: True)
        chunk_strategy: Chunking strategy ("tokens", "slide"). Default from config.
        chunk_config: Optional chunking configuration dict
        embedding_name: Optional embedding name (e.g., "openai-small")
        embedding_provider: Optional embedding provider (e.g., "openai")
        embedding_model: Optional embedding model (e.g., "text-embedding-3-small")
        session: Optional database session

    Returns:
        Dictionary with:
        - document_ids: List of created Document IDs
        - num_documents: Number of documents created
        - parsed: Whether documents were parsed
        - num_summaries: Number of summaries generated (if generate_summary=True)
        - num_metadata_enriched: Number of documents with structured metadata enrichment
        - num_chunks: Total number of chunks created (if chunk_and_embed=True)
        - num_text_chunks: Number of text document chunks (if chunk_and_embed=True)
        - num_summary_chunks: Number of summary chunks (if chunk_and_embed=True and generate_summary=True)
        - num_image_chunks: Number of image chunks (if chunk_and_embed=True)

    Example:
        ```python
        from kb_mcp.kb.tools import ingest

        # Full workflow with defaults (parse + summarize + chunk/embed)
        result = ingest(
            "paper.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
        )
        print(f"Created {result['num_documents']} documents with {result['num_chunks']} chunks")

        # Just parse and add (no summary, no embedding)
        result = ingest(
            "paper.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
            generate_summary=False,
            chunk_and_embed=False,
        )

        # Parse + summarize only (no embedding)
        result = ingest(
            "paper.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
            chunk_and_embed=False,
        )

        # Disable summary chunks
        result = ingest(
            "paper.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
            create_summary_chunks=False,
        )
        ```
    """
    from .documents import add_document, get

    # Step 1: Add document (parse and create Document records)
    with get_db_session(session) as session:
        add_result = add_document(
            file_path,
            source_id=source_id,
            doc_id=doc_id,
            parser_name=parser_name,
            extract_images=extract_images,
            describe_images=describe_images,
            dedup_level=dedup_level,
            force_reparse=force_reparse,
            copy_to_kb=copy_to_kb,
            uri=uri,
            meta=meta,
            session=session,
        )

        # Initialize result dictionary by merging add_result with additional fields
        result = add_result | {
            "num_summaries": 0,
            "num_metadata_enriched": 0,
            "num_chunks": 0,
            "num_text_chunks": 0,
            "num_summary_chunks": 0,
            "num_image_chunks": 0,
        }

        # If file was skipped (already processed), return early
        if add_result.get("skipped", False):
            return result

        # If no documents were created, return early
        if not add_result["document_ids"]:
            return result

        # Retrieve documents for further processing
        docs = get(uid=add_result["document_ids"], session=session)
        if docs is None:
            return result

        # Ensure docs is a list
        if not isinstance(docs, list):
            docs = [docs]

        # Separate text and image documents
        text_documents = [doc for doc in docs if doc.doc_type != "image"]
        image_documents = [doc for doc in docs if doc.doc_type == "image"]

        # Step 2: Generate summaries (if requested)
        if generate_summary and text_documents:
            for doc in text_documents:
                try:
                    doc.generate_summary(
                        include_title=True,
                        include_gist=True,
                        include_summary=True,
                        include_metadata=summary_include_metadata,
                    )
                    result["num_summaries"] += 1
                    if summary_include_metadata and isinstance(doc.meta, dict) and doc.meta.get("metadata_enriched"):
                        result["num_metadata_enriched"] += 1
                    logger.info(f"Generated summary for document {doc.id}")
                except Exception as e:
                    logger.warning(f"Could not generate summary for document {doc.id}: {e}")

        # Step 3: Chunk and embed (if requested)
        if chunk_and_embed:
            # Chunk and embed text documents
            for doc in text_documents:
                try:
                    chunks = doc.chunk_and_embed(
                        chunk_strategy=chunk_strategy,
                        chunk_config=chunk_config,
                        embedding_name=embedding_name,
                        provider=embedding_provider,
                        model=embedding_model,
                    )
                    if chunks:
                        result["num_text_chunks"] += len(chunks)
                        result["num_chunks"] += len(chunks)
                        logger.info(f"Chunked and embedded document {doc.id} ({len(chunks)} chunks)")

                    # Also create summary chunks if summary was generated and enabled
                    if generate_summary and create_summary_chunks and doc.summary:
                        summary_chunks = doc.chunk_and_embed(
                            chunk_strategy="summary",
                            embedding_name=embedding_name,
                            provider=embedding_provider,
                            model=embedding_model,
                        )
                        if summary_chunks:
                            result["num_summary_chunks"] += len(summary_chunks)
                            result["num_chunks"] += len(summary_chunks)
                            logger.info(f"Created and embedded summary chunk for document {doc.id}")
                except Exception as e:
                    logger.error(f"Error chunking/embedding document {doc.id}: {e}", exc_info=True)

            # Chunk and embed image documents
            for doc in image_documents:
                try:
                    chunks = doc.chunk_and_embed(
                        chunk_strategy="image",
                        embedding_name=embedding_name,
                        provider=embedding_provider,
                        model=embedding_model,
                    )
                    if chunks:
                        result["num_image_chunks"] += len(chunks)
                        result["num_chunks"] += len(chunks)
                        logger.info(f"Chunked and embedded image document {doc.id}")
                except Exception as e:
                    logger.error(f"Error chunking/embedding image document {doc.id}: {e}", exc_info=True)

        session.commit()

    return result


def parse_all(
    source_id: Optional[str] = None,
    parser_name: str = "kb-mcp",
    extract_images: Optional[bool] = None,
    describe_images: Optional[bool] = None,
    force_reparse: bool = False,
    batch_size: Optional[int] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Parse all raw documents that don't have corresponding processed documents yet.

    Uses a LEFT JOIN to efficiently find RawDocuments without corresponding Documents
    for the specified parser.

    Args:
        source_id: Optional source identifier to filter by. If None, processes all sources.
        parser_name: Parser to use (default: "kb-mcp")
        extract_images: If True, create separate Document objects for extracted images.
                       If None, reads from PARSE_IMAGE_ADDITIONAL_DOC env var.
        describe_images: If True, generate LLM descriptions for images using vision model.
                        If None, reads from PARSE_IMAGE_LLM_DESCRIPTION env var.
        force_reparse: If True, re-parse even if documents already exist for this parser.
        batch_size: If provided, process in batches with LOCK/SKIP LOCKED for parallel processing.
                   If None, uses default from get_batch_config()['parse_batch_size'].
                   Set to a large value (e.g., 999999) to disable batching and process all at once.
        limit: If provided, stop after parsing this many documents (useful for testing).

    Returns:
        Dictionary with:
        - total_raw: Total number of RawDocuments found
        - parsed: Number of documents successfully parsed
        - skipped: Number of documents skipped (file not found)
        - errors: Number of errors encountered
        - document_ids: List of created Document IDs
    """
    from pathlib import Path
    from sqlalchemy import and_
    from .db_models import RawDocument
    from .documents import add_document

    from ..config import get_batch_config

    batch_size = batch_size or get_batch_config()['parse_batch_size']

    parsed = 0
    skipped = 0
    errors = 0
    document_ids = []
    total_processed = 0

    # Process in batches with lock/commit cycle
    while True:
        with get_db_session(auto_expunge=False) as session:
            # Query for RawDocuments that don't have Documents with the specified parser
            # Use NOT EXISTS instead of LEFT JOIN to avoid FOR UPDATE on outer join
            subquery = session.query(Document.id).filter(
                and_(
                    Document.raw_document_id == RawDocument.id,
                    Document.parser_id == parser_name
                )
            ).exists()

            query = session.query(RawDocument).filter(~subquery)

            if source_id:
                query = query.filter(RawDocument.source_id == source_id)

            # For parallel processing: lock a batch of rows
            # SKIP LOCKED allows other workers to grab different rows
            effective_batch = batch_size
            if limit is not None:
                effective_batch = min(batch_size, limit - total_processed)
            raw_docs = query.limit(effective_batch).with_for_update(skip_locked=True).all()

            if not raw_docs:
                # No more documents to process
                break

            if total_processed == 0:
                logger.info(f"Processing documents in batches of {batch_size}")

            # Parse inside the same session so the FOR UPDATE row locks are held
            # across the whole batch. We commit after each document so results are
            # saved incrementally — if the process dies mid-batch, already-parsed
            # documents are preserved. The locks on the remaining rows are released
            # when the connection drops, freeing them for other workers.
            batch_num = total_processed // batch_size + 1
            for raw_doc in tqdm(raw_docs, desc=f"Parsing documents (batch {batch_num})", unit="doc"):
                try:
                    if not raw_doc.file_path:
                        logger.warning(f"Raw document {raw_doc.id} has no file_path, skipping")
                        skipped += 1
                        continue

                    file_path = Path(raw_doc.file_path)

                    if not file_path.exists():
                        logger.warning(f"Skipping raw_doc {raw_doc.id}: file not found at {raw_doc.file_path}")
                        skipped += 1
                        continue

                    logger.debug(f"Parsing raw document {raw_doc.id}: {raw_doc.file_path}")
                    # For marker-preloaded parser, we don't need the original PDF file
                    # We only need the filename stem to find the Marker output directory
                    if parser_name != "marker-preloaded":
                        # Check if file exists (only for parsers that need the actual file)
                        if not file_path.exists():
                            logger.warning(f"Skipping raw_doc {raw_doc.id}: file not found at {raw_doc.file_path}")
                            skipped += 1
                            continue
                        logger.debug(f"Parsing raw document {raw_doc.id}: {raw_doc.file_path}")
                    else:
                        # For marker-preloaded, log the expected output directory instead
                        logger.debug(f"Parsing raw document {raw_doc.id} from stem: {file_path.stem}")

                    result = add_document(
                        file_path,
                        source_id=raw_doc.source_id,
                        doc_id=raw_doc.doc_id,
                        meta=raw_doc.meta,
                        parser_name=parser_name,
                        extract_images=extract_images,
                        describe_images=describe_images,
                        force_reparse=force_reparse,
                        copy_to_kb=False,
                        session=session,
                    )

                    document_ids.extend(result["document_ids"])
                    parsed += 1
                    logger.debug(f"Successfully parsed {result['num_documents']} document(s) from {raw_doc.file_path}")

                except FileNotFoundError as e:
                    # For marker-preloaded, this means Marker output doesn't exist
                    # Skip this document and don't retry
                    skipped += 1
                    logger.debug(f"Skipping raw document {raw_doc.id}: {e}")
                    continue
                except Exception as e:
                    errors += 1
                    logger.error(f"Error parsing raw document {raw_doc.id}: {e}", exc_info=True)
                    continue

            total_processed += len(raw_docs)
            # Commit here — releases all FOR UPDATE locks only after the full batch
            # is parsed and all Document rows are visible to other workers.
            if limit is not None and total_processed >= limit:
                break

    if total_processed == 0:
        logger.info(f"No unparsed raw documents found{' for source_id: ' + source_id if source_id else ''}")

    logger.info(
        f"Parse complete: {parsed} parsed, {skipped} skipped, {errors} errors"
    )

    return {
        "total_raw": total_processed,
        "parsed": parsed,
        "skipped": skipped,
        "errors": errors,
        "document_ids": document_ids,
    }


def chunk_and_embed_all(
    source_id: str,
    chunk_strategy: Optional[str] = None,
    chunk_config: Optional[Dict[str, Any]] = None,
    include_images: bool = True,
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    parser_name: Optional[str] = None,
    force: bool = False,
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
        parser_name: Optional parser ID to filter documents by (e.g., "marker", "nougat", "docling").

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
    
    logger.info(f"Starting chunk_and_embed_all for source_id: {source_id}" + (f", parser_name: {parser_name}" if parser_name else ""))

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

        if parser_name is not None:
            query = query.filter(Document.parser_id == parser_name)
        
        # Determine the actual strategy name that will be created
        chunk_strategy = chunk_strategy or get_embedding_config()['chunk_strategy']
        strategy_full_name = get_strategy_name(chunk_strategy, chunk_config)

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
                    Chunk.chunk_strategy == strategy_full_name
                )
            ).filter(Chunk.id.is_(None))
        else:
            if not force:
                # For other strategies: find documents without chunks of this specific strategy
                # This allows creating multiple strategies for the same documents (e.g., tokens and tokens_no_gist)
                from sqlalchemy import and_
                query = query.outerjoin(
                    Chunk,
                    and_(
                        Document.id == Chunk.document_id,
                        Chunk.chunk_strategy == strategy_full_name
                    )
                ).filter(Chunk.id.is_(None))
        
        documents = query.all()
        
        if not documents:
            logger.info(f"No documents found for source_id: {source_id}" + (f", parser_name: {parser_name}" if parser_name else "") + " that need chunking")
            return {
                "processed": 0,
                "chunked": 0,
                "skipped": 0,
                "errors": 0,
            }

        logger.info(f"Found {len(documents)} document(s) for source_id: {source_id}" + (f", parser_name: {parser_name}" if parser_name else "") + " that need chunking")

        processed = 0
        chunked = 0
        skipped = 0
        errors = 0

        # Use tqdm progress bar for better user experience
        for doc in tqdm(documents, desc="Chunking and embedding", unit="doc"):
            try:
                processed += 1

                # Drop existing chunks for this strategy if force mode
                if force:
                    dropped = doc.drop_chunks(chunk_strategy=strategy_full_name)
                    if dropped:
                        logger.debug(f"Dropped {dropped} existing chunks for document {doc.id}")

                # Chunk and embed the document using the document method
                logger.debug(f"Chunking and embedding document {doc.id} ({doc.doc_id or doc.id})")
                chunks = doc.chunk_and_embed(
                    chunk_strategy=chunk_strategy,
                    chunk_config=chunk_config,
                    embedding_name=embedding_name,
                    provider=provider,
                    model=model,
                )

                if chunks:
                    chunked += 1
                    logger.debug(f"Successfully chunked and embedded document {doc.id} ({len(chunks)} chunks)")
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
                logger.debug(f"Chunking and embedding image document {doc.id} ({doc.doc_id or doc.id})")
                chunks = doc.chunk_and_embed(
                    chunk_strategy="image",
                    embedding_name=embedding_name,
                    provider=provider,
                    model=model,
                )

                if chunks:
                    chunked += 1
                    logger.debug(f"Successfully chunked and embedded image document {doc.id}")
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
    parser_name: Optional[str] = None,
    batch_size: int = 10,
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
        parser_name: Optional parser ID to filter documents by (e.g., "marker", "nougat", "docling").

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

    logger.info(f"Starting summarize_all for source_id: {source_id}" + (f", parser_name: {parser_name}" if parser_name else ""))

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

        if parser_name is not None:
            query = query.filter(Document.parser_id == parser_name)

        documents = query.all()

        if not documents:
            logger.info(f"No documents found for source_id: {source_id}" + (f", parser_name: {parser_name}" if parser_name else "") + " that need summarization")
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

        # Use tqdm progress bar for better user experience
        for doc in tqdm(documents, desc="Summarizing documents", unit="doc"):
            try:
                processed += 1

                # Generate summary using the document method
                logger.debug(f"Generating summary for document {doc.id} ({doc.doc_id or doc.id})")
                doc.generate_summary(
                    include_title=True,
                    include_gist=True,
                    include_summary=True,
                    model=model,
                )

                summarized += 1
                if summarized % batch_size == 0:
                    session.commit()
                    logger.debug(f"Committed batch of {batch_size} summaries")
                logger.debug(f"Successfully generated summary for document {doc.id}")

                # Optionally create summary chunk
                if create_summary_chunk and doc.summary:
                    logger.debug(f"Creating summary chunk for document {doc.id}")

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
                            logger.debug(f"Created and embedded summary chunk for document {doc.id}")
                    else:
                        # Just create the chunk without embedding
                        chunks = doc.chunk(chunk_strategy="summary")
                        if chunks:
                            chunked += 1
                            logger.debug(f"Created summary chunk for document {doc.id}")

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


def filter_all(
    source_id: Optional[str] = None,
    parser_name: str = "marker",
    model: Optional[str] = None,
    batch_size: int = 10,
    limit: Optional[int] = None,
    delay: float = 0.0,
) -> Dict[str, Any]:
    """Run the LLM privacy filter over all raw documents that haven't been classified yet.

    For each unfiltered RawDocument, finds the corresponding parsed Document (from
    `parser_name`, default 'marker') and sends its text to the privacy classifier.
    Results are stored in the `privacy_filters` table.

    Already-filtered documents (those with an existing PrivacyFilter row) are skipped.

    Supports parallel workers: each batch is locked with FOR UPDATE SKIP LOCKED so
    multiple `filter_all` processes can run concurrently without double-processing.

    Args:
        source_id: Optional source identifier. If None, processes all sources.
        parser_name: Parser whose text to use for classification (default: 'marker').
        model: LLM model to use (overrides PRIVACY_FILTER_MODEL env var).
        batch_size: Number of documents to lock and process per batch (default: 10).
        limit: Stop after classifying this many documents total (useful for testing).

    Returns:
        Dictionary with:
        - processed: Number of raw documents examined
        - filtered: Number that were successfully classified
        - skipped: Number skipped (no parsed text found)
        - errors: Number of failures
        - by_label: Dict mapping label → count for this run
    """
    from sqlalchemy import and_
    from .db_models import RawDocument
    from ..privacy import classify_privacy

    logger.info(
        f"Starting filter_all - source_id: {source_id or 'all'}, parser: {parser_name}, "
        f"batch_size: {batch_size}"
    )

    processed = 0
    filtered = 0
    skipped = 0
    errors = 0
    by_label: Dict[str, int] = {
        PrivacyFilter.LABEL_PUBLIC: 0,
        PrivacyFilter.LABEL_NEEDS_REVIEW: 0,
        PrivacyFilter.LABEL_PRIVATE: 0,
    }

    while True:
        with get_db_session(auto_expunge=False) as session:
            # Find RawDocuments without a PrivacyFilter row using NOT EXISTS
            already_filtered = session.query(PrivacyFilter.raw_document_id).filter(
                PrivacyFilter.raw_document_id == RawDocument.id
            ).exists()

            query = session.query(RawDocument).filter(~already_filtered)
            if source_id:
                query = query.filter(RawDocument.source_id == source_id)

            effective_batch = batch_size
            if limit is not None:
                remaining = limit - (filtered + skipped + errors)
                if remaining <= 0:
                    break
                effective_batch = min(batch_size, remaining)

            # SKIP LOCKED: parallel workers each grab a different batch
            raw_docs = (
                query
                .order_by(RawDocument.source_id, RawDocument.doc_id)
                .limit(effective_batch)
                .with_for_update(skip_locked=True)
                .all()
            )

            if not raw_docs:
                break

            batch_num = (processed // batch_size) + 1
            for raw_doc in tqdm(raw_docs, desc=f"Privacy filtering (batch {batch_num})", unit="doc"):
                processed += 1
                try:
                    parsed_doc = session.query(Document).filter(
                        and_(
                            Document.raw_document_id == raw_doc.id,
                            Document.parser_id == parser_name,
                            Document.doc_type != "image",
                            Document.text.isnot(None),
                            Document.text != "",
                        )
                    ).first()

                    if parsed_doc is None:
                        logger.debug(
                            f"No parsed text found for raw_doc {raw_doc.id} "
                            f"(parser={parser_name}), skipping"
                        )
                        skipped += 1
                        # Insert a sentinel so parallel workers don't re-attempt this doc.
                        # Label needs_review means a human can decide later.
                        pf = PrivacyFilter(
                            raw_document_id=raw_doc.id,
                            document_id=None,
                            label=PrivacyFilter.LABEL_NEEDS_REVIEW,
                            reasoning="No parsed text available for this parser; requires manual review.",
                            model=None,
                            meta={"parser_name": parser_name, "skipped": True},
                        )
                        session.add(pf)
                        continue

                    result = classify_privacy(parsed_doc.text, model=model)

                    pf = PrivacyFilter(
                        raw_document_id=raw_doc.id,
                        document_id=parsed_doc.id,
                        label=result["label"],
                        reasoning=result["reasoning"],
                        model=result["model"],
                        meta={
                            "time_seconds": result["time_seconds"],
                            "parser_name": parser_name,
                        },
                    )
                    session.add(pf)
                    filtered += 1
                    by_label[result["label"]] = by_label.get(result["label"], 0) + 1

                except Exception as e:
                    errors += 1
                    logger.error(f"Error classifying raw_doc {raw_doc.id}: {e}", exc_info=True)
                    # Write a sentinel so this doc isn't re-attempted in the next batch loop.
                    # Without this, failed docs loop forever because no PrivacyFilter row exists.
                    try:
                        pf_err = PrivacyFilter(
                            raw_document_id=raw_doc.id,
                            document_id=parsed_doc.id if parsed_doc else None,
                            label=PrivacyFilter.LABEL_NEEDS_REVIEW,
                            reasoning=f"Classification failed with error: {type(e).__name__}: {e}",
                            model=model,
                            meta={"parser_name": parser_name, "error": True},
                        )
                        session.add(pf_err)
                    except Exception as inner_e:
                        logger.error(f"Failed to write error sentinel for {raw_doc.id}: {inner_e}")

            # Commit releases the FOR UPDATE locks, making results visible to other workers
            session.commit()

        if limit is not None and (filtered + skipped + errors) >= limit:
            break

        if delay > 0:
            import time
            logger.debug(f"Sleeping {delay}s between batches")
            time.sleep(delay)

    logger.info(
        f"Completed filter_all - processed: {processed}, filtered: {filtered}, "
        f"skipped: {skipped}, errors: {errors}, labels: {by_label}"
    )

    return {
        "processed": processed,
        "filtered": filtered,
        "skipped": skipped,
        "errors": errors,
        "by_label": by_label,
    }


def export_source(
    source_id: str,
    output_dir: Union[str, Path],
    parser_name: str = "marker",
    include_private: bool = False,
    include_needs_review: bool = False,
    private_subdir: str = "private",
    needs_review_subdir: str = "needs_review",
) -> Dict[str, Any]:
    """Export documents from a source into a folder hierarchy.

    For each raw document, creates a subfolder at:
        <output_dir>/<doc_id>/

    Folder contents (text only, no images):
        original.pdf          — copy of the raw file (if available and is a PDF)
        text_<parser>.md/txt  — extracted text for each available parser
        description.txt       — document description from parser comparison (if available)
        summary.txt           — LLM-generated summary (from the marker-parsed document)
        gist.txt              — LLM-generated gist
        title.txt             — extracted title and/or LLM-generated title
        metadata.json         — all metadata fields

    Privacy filtering controls which documents are exported:
        - public documents → <output_dir>/<doc_id>/
        - needs_review documents → <output_dir>/<needs_review_subdir>/<doc_id>/  (if include_needs_review)
        - private documents → <output_dir>/<private_subdir>/<doc_id>/  (if include_private)

    If a document has no PrivacyFilter entry it is treated as needs_review.

    Args:
        source_id: Source identifier to export.
        output_dir: Root directory to write export into (created if it doesn't exist).
        parser_name: Preferred parser for summary/gist/title (default: 'marker').
        include_private: If True, also export private documents into a separate subfolder.
        include_needs_review: If True, also export needs_review documents into a separate subfolder.
        private_subdir: Subfolder name for private documents (default: 'private').
        needs_review_subdir: Subfolder name for needs_review documents (default: 'needs_review').

    Returns:
        Dictionary with:
        - exported_public: Number of public documents exported
        - exported_needs_review: Number of needs_review documents exported (0 if include_needs_review=False)
        - exported_private: Number of private documents exported (0 if include_private=False)
        - skipped_private: Number of private documents skipped
        - skipped_needs_review: Number of needs_review documents skipped
        - errors: Number of documents that failed during export
    """
    from .db_models import RawDocument

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exported_public = 0
    exported_needs_review = 0
    exported_private = 0
    skipped_private = 0
    skipped_needs_review = 0
    errors = 0

    with get_db_session() as session:
        raw_docs = (
            session.query(RawDocument)
            .filter(RawDocument.source_id == source_id)
            .order_by(RawDocument.doc_id)
            .all()
        )

        if not raw_docs:
            logger.info(f"No raw documents found for source_id: {source_id}")
            return {
                "exported_public": 0,
                "exported_needs_review": 0,
                "exported_private": 0,
                "skipped_private": 0,
                "skipped_needs_review": 0,
                "errors": 0,
            }

        logger.info(f"Exporting {len(raw_docs)} raw document(s) for source_id: {source_id}")

        for raw_doc in tqdm(raw_docs, desc="Exporting", unit="doc"):
            try:
                # Determine privacy label
                pf = (
                    session.query(PrivacyFilter)
                    .filter(PrivacyFilter.raw_document_id == raw_doc.id)
                    .order_by(PrivacyFilter.created_time.desc())
                    .first()
                )
                label = pf.label if pf else PrivacyFilter.LABEL_NEEDS_REVIEW

                # Parser comparison description (most recent)
                from .db_models import ParserComparison
                pc = (
                    session.query(ParserComparison)
                    .filter(
                        ParserComparison.raw_document_id == raw_doc.id,
                        ParserComparison.document_description.isnot(None),
                    )
                    .order_by(ParserComparison.created_time.desc())
                    .first()
                )
                document_description = pc.document_description if pc else None

                # Routing decision
                if label == PrivacyFilter.LABEL_PRIVATE:
                    if not include_private:
                        skipped_private += 1
                        continue
                    doc_root = output_dir / private_subdir
                elif label == PrivacyFilter.LABEL_NEEDS_REVIEW:
                    if not include_needs_review:
                        skipped_needs_review += 1
                        continue
                    doc_root = output_dir / needs_review_subdir
                else:
                    doc_root = output_dir

                # Sanitize doc_id for filesystem use
                safe_doc_id = (raw_doc.doc_id or raw_doc.id).replace("/", "_").replace("\\", "_")
                doc_dir = doc_root / safe_doc_id
                doc_dir.mkdir(parents=True, exist_ok=True)

                # 1. Copy original file if available and accessible
                if raw_doc.file_path:
                    src_path = Path(raw_doc.file_path)
                    if src_path.exists():
                        ext = src_path.suffix.lower()
                        dest_name = f"original{ext}" if ext else "original"
                        shutil.copy2(src_path, doc_dir / dest_name)

                # 2. Write extracted text for each available parser (text only, skip images)
                parsed_docs = (
                    session.query(Document)
                    .filter(
                        Document.raw_document_id == raw_doc.id,
                        Document.doc_type != "image",
                        Document.text.isnot(None),
                        Document.text != "",
                    )
                    .all()
                )

                preferred_doc = None
                for doc in parsed_docs:
                    p_name = doc.parser_id or "unknown"
                    ext = ".md" if p_name == "marker" else ".txt"
                    (doc_dir / f"text_{p_name}{ext}").write_text(doc.text, encoding="utf-8")
                    if p_name == parser_name:
                        preferred_doc = doc

                # Fall back to any doc if preferred parser not found
                if preferred_doc is None and parsed_docs:
                    preferred_doc = parsed_docs[0]

                # 3. Summary, gist, title from preferred doc
                if document_description:
                    (doc_dir / "description.txt").write_text(document_description, encoding="utf-8")

                if preferred_doc:
                    if preferred_doc.summary:
                        (doc_dir / "summary.txt").write_text(preferred_doc.summary, encoding="utf-8")
                    if preferred_doc.gist:
                        (doc_dir / "gist.txt").write_text(preferred_doc.gist, encoding="utf-8")

                    title_parts: List[str] = []
                    if preferred_doc.title:
                        title_parts.append(f"title: {preferred_doc.title}")
                    if preferred_doc.title_gen:
                        title_parts.append(f"title_gen: {preferred_doc.title_gen}")
                    if title_parts:
                        (doc_dir / "title.txt").write_text("\n".join(title_parts), encoding="utf-8")

                # 4. Metadata JSON
                raw_meta = raw_doc.meta or {}
                doc_meta = preferred_doc.meta or {} if preferred_doc else {}
                meta: Dict[str, Any] = {
                    "raw_document_id": raw_doc.id,
                    "source_id": raw_doc.source_id,
                    "doc_id": raw_doc.doc_id,
                    "source_type": raw_doc.source_type,
                    "file_size": raw_doc.file_size,
                    # fields from raw_meta / doc_meta worth surfacing
                    "filename": doc_meta.get("filename") or raw_meta.get("filename"),
                    "authors": doc_meta.get("authors") or raw_meta.get("authors"),
                    "publication_date": doc_meta.get("publication_date") or raw_meta.get("publication_date"),
                    "parsers_available": [d.parser_id for d in parsed_docs if d.parser_id],
                }
                # remove None-valued keys to keep the file clean
                meta = {k: v for k, v in meta.items() if v is not None}
                if document_description:
                    meta["description"] = document_description
                if preferred_doc:
                    meta["title"] = preferred_doc.title
                    meta["title_gen"] = preferred_doc.title_gen
                    meta["summary"] = preferred_doc.summary
                    meta["gist"] = preferred_doc.gist

                (doc_dir / "metadata.json").write_text(
                    json.dumps(meta, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )

                # Increment counters
                if label == PrivacyFilter.LABEL_PRIVATE:
                    exported_private += 1
                elif label == PrivacyFilter.LABEL_NEEDS_REVIEW:
                    exported_needs_review += 1
                else:
                    exported_public += 1

            except Exception as e:
                errors += 1
                logger.error(f"Error exporting raw_doc {raw_doc.id}: {e}", exc_info=True)
                continue

    logger.info(
        f"Export complete for source_id: {source_id}. "
        f"public={exported_public}, needs_review={exported_needs_review}, "
        f"private={exported_private}, skipped_private={skipped_private}, "
        f"skipped_needs_review={skipped_needs_review}, errors={errors}"
    )

    return {
        "exported_public": exported_public,
        "exported_needs_review": exported_needs_review,
        "exported_private": exported_private,
        "skipped_private": skipped_private,
        "skipped_needs_review": skipped_needs_review,
        "errors": errors,
    }


