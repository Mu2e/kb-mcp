"""Knowledge base operations - simple get and add functions."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ..database import get_db_session, init_db
from ..db_models import Document, Source

logger = logging.getLogger(__name__)

# Track if database has been initialized
_db_initialized = False

# Deduplication levels
DEDUP_NONE = 0           # Always insert (no checking)
DEDUP_IDENTITY = 1       # Check (source_id, doc_id, parser_id), update if exists (DEFAULT)
DEDUP_HASH = 2           # Check content_hash, skip/update if exists
DEDUP_IDENTITY_HASH = 3  # Check identity first, then hash (most strict)

# Legacy aliases for backward compatibility
DEDUP_LEVEL_INSERT = 0
DEDUP_LEVEL_WARN = 1
DEDUP_LEVEL_OVERWRITE_HASH = 2
DEDUP_LEVEL_OVERWRITE_HASH_WARN = 3


def _get_default_dedup_level() -> int:
    """Get default deduplication level from environment variable.

    Returns:
        Deduplication level (0-3), default is 1 (DEDUP_IDENTITY)
    """
    # KB deduplication level is no longer in config - default to DEDUP_IDENTITY
    # This can be added to config if needed
    return DEDUP_IDENTITY


def _find_duplicates(
    document: Document,
    session: Any,
) -> Tuple[Optional[Document], Optional[Document]]:
    """Find duplicate documents by identity and by content_hash.

    Args:
        document: Document to check for duplicates
        session: Database session

    Returns:
        Tuple of (existing_by_identity, existing_by_hash):
        - existing_by_identity: Document with same (source_id, doc_id, parser_id), or None
        - existing_by_hash: Document with same content_hash, or None
    """
    existing_by_identity = None
    existing_by_hash = None

    # Check for existing document with same identity (source_id, doc_id, parser_id)
    if document.source_id and document.doc_id:
        query = session.query(Document).filter(
            Document.source_id == document.source_id,
            Document.doc_id == document.doc_id,
        )
        # Include parser_id in identity check (allows same doc parsed with different frameworks)
        if document.parser_id:
            query = query.filter(Document.parser_id == document.parser_id)
        else:
            # If parser_id is None, match documents with NULL parser_id
            query = query.filter(Document.parser_id.is_(None))

        existing_by_identity = query.first()

    # Check for existing document with same content_hash
    if document.content_hash:
        existing_by_hash = (
            session.query(Document)
            .filter(Document.content_hash == document.content_hash)
            .first()
        )

    return existing_by_identity, existing_by_hash


def _update_document(existing: Document, new: Document, session: Any, update_hash: bool = False, commit: bool = True) -> Document:
    """Update existing document with new document's data.
    
    Args:
        existing: Existing document to update
        new: New document with updated data
        session: Database session
        update_hash: If True, also update content_hash; otherwise keep existing hash
        commit: If True, commit the session. If False, caller is responsible for committing.
    
    Returns:
        Updated document (detached from session if commit=True, otherwise attached)
    """
    existing.source_id = new.source_id
    existing.doc_id = new.doc_id
    existing.uri = new.uri
    existing.source_type = new.source_type
    existing.doc_type = new.doc_type
    existing.text = new.text
    existing.binary = new.binary
    existing.meta = new.meta
    existing.creating_time = new.creating_time
    existing.update_time = new.update_time
    existing.parent_id = new.parent_id
    if update_hash:
        existing.content_hash = new.content_hash
    if commit:
        session.commit()
        session.refresh(existing)
        session.expunge(existing)
    else:
        session.flush()  # Flush changes but don't commit
        session.refresh(existing)
    return existing


def _handle_duplicate(
    document: Document,
    existing_by_identity: Optional[Document],
    existing_by_hash: Optional[Document],
    dedup_level: int,
    session: Any,
    commit: bool = True,
) -> Optional[Document]:
    """Handle duplicate document based on deduplication level.

    Args:
        document: New document to add
        existing_by_identity: Existing document with same (source_id, doc_id, parser_id), or None
        existing_by_hash: Existing document with same content_hash, or None
        dedup_level: Deduplication level (0-3)
        session: Database session
        commit: If True, commit the session. If False, caller is responsible for committing.

    Returns:
        Document object (existing or new), or None if should insert new
    """
    # Level 0 (DEDUP_NONE): Always insert, no checking
    if dedup_level == DEDUP_NONE:
        return None

    # Level 1 (DEDUP_IDENTITY): Check identity, update if exists
    if dedup_level == DEDUP_IDENTITY:
        if existing_by_identity:
            updated = _update_document(existing_by_identity, document, session, update_hash=True, commit=commit)
            logger.info(
                f"Updated document {updated.id} "
                f"(same identity: {document.source_id}/{document.doc_id}/{document.parser_id})"
            )
            return updated
        return None

    # Level 2 (DEDUP_HASH): Check hash, update if exists
    if dedup_level == DEDUP_HASH:
        if existing_by_hash:
            updated = _update_document(existing_by_hash, document, session, update_hash=False, commit=commit)
            logger.info(
                f"Updated document {updated.id} (same content_hash: {document.content_hash[:16] if document.content_hash else 'N/A'}...)"
            )
            return updated
        return None

    # Level 3 (DEDUP_IDENTITY_HASH): Check identity first, then hash
    if dedup_level == DEDUP_IDENTITY_HASH:
        if existing_by_identity:
            updated = _update_document(existing_by_identity, document, session, update_hash=True, commit=commit)
            logger.info(
                f"Updated document {updated.id} "
                f"(same identity: {document.source_id}/{document.doc_id}/{document.parser_id})"
            )
            return updated
        if existing_by_hash:
            updated = _update_document(existing_by_hash, document, session, update_hash=False, commit=commit)
            logger.info(
                f"Updated document {updated.id} (same content_hash: {document.content_hash[:16] if document.content_hash else 'N/A'}...)"
            )
            return updated
        return None

    # Unknown level - default to insert
    logger.warning(f"Unknown deduplication level: {dedup_level}, defaulting to insert")
    return None


def _ensure_db_initialized() -> None:
    """Ensure database is initialized (lazy loading)."""
    global _db_initialized
    if not _db_initialized:
        init_db(create_tables=True)
        _db_initialized = True


def _sanitize_text(text: Optional[str]) -> Optional[str]:
    """Remove NULL bytes from text (PostgreSQL doesn't allow them).
    
    Args:
        text: Text string that may contain NULL bytes
        
    Returns:
        Text with NULL bytes removed, or None if input was None
    """
    if text is None:
        return None
    return text.replace('\x00', '')


def _sanitize_document(document: Document) -> None:
    """Sanitize document fields to remove NULL bytes and other problematic characters.
    
    Args:
        document: Document object to sanitize
    """
    if document.text:
        document.text = _sanitize_text(document.text)
    if document.title:
        document.title = _sanitize_text(document.title)


def _compute_hash(document: Document) -> None:
    """Compute and set SHA256 hash for document content.

    Args:
        document: Document object to compute hash for
    """
    if document.text:
        content = document.text.encode("utf-8")
    elif document.binary:
        content = document.binary
    else:
        return  # No content to hash

    document.content_hash = hashlib.sha256(content).hexdigest()


def add_parsed(
    data: Union[Document, Dict[str, Any]],
    *,
    dedup_level: Optional[int] = None,
    session: Optional[Any] = None,
) -> Document:
    """Add a pre-built document to the database (low-level helper).

    Usage:
        ```python
        # Add from dict (recommended)
        doc = add_parsed({
            "source_id": "mu2e-docdb",
            "source_type": "application/pdf",
            "text": "Content...",
        })

        # Add Document object
        doc_obj = Document.from_dict({...})
        doc = add_parsed(doc_obj)
        
        # With custom deduplication level
        doc = add_parsed(data, dedup_level=4)
        ```

    Args:
        data: Document object or dictionary with document data
        dedup_level: Deduplication level (0-4). If None, uses KB_DEDUPLICATION_LEVEL env var or default 2.
                     - 0: Insert duplicates, no warnings
                     - 1: Insert with warnings
                     - 2: Overwrite if same hash (default)
                     - 3: Same as 2 but with warnings for existing source_id, doc_id
                     - 4: Overwrite same hash and same source_id, doc_id
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        Created or updated Document object

    Raises:
        ValueError: If data is not Document or dict, or if both text and binary are None
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()

    # Get deduplication level
    if dedup_level is None:
        dedup_level = _get_default_dedup_level()

    # Handle input formats
    if isinstance(data, Document):
        document = data
    elif isinstance(data, dict):
        document = Document.from_dict(data)
    else:
        raise ValueError("data must be a Document object or dictionary")

    # Validation (Document.from_dict already validates, but double-check)
    if document.text is None and document.binary is None:
        raise ValueError("Either text or binary must be provided")

    if document.doc_type == "text" and document.text is None:
        raise ValueError("text must be provided for text documents")

    if document.doc_type == "image" and document.binary is None:
        raise ValueError("binary must be provided for image documents")

    # Sanitize document fields (remove NULL bytes, etc.)
    _sanitize_document(document)
    
    # Always compute content hash
    _compute_hash(document)

    # Determine if we own the session (for commit handling)
    should_close = session is None

    # Save to database
    with get_db_session(session) as session:
        # Check that source exists
        source = session.query(Source).filter(Source.id == document.source_id).first()
        if not source:
            raise ValueError(
                f"Source '{document.source_id}' does not exist. "
                f"Create it first using add_source('{document.source_id}', ...)"
            )

        # Check for duplicates
        if dedup_level >= DEDUP_LEVEL_INSERT:
            existing_by_id, existing_by_hash = _find_duplicates(document, session)
        else:
            existing_by_id, existing_by_hash = None, None

        # Handle duplicate based on level
        existing_doc = _handle_duplicate(
            document, existing_by_id, existing_by_hash, dedup_level, session, commit=should_close
        )

        if existing_doc is not None:
            # Document was updated, return it
            return existing_doc

        # No duplicate or level 0/1 - proceed with insert
        session.add(document)
        session.flush()  # Get the ID
        # Refresh to ensure all attributes are loaded
        session.refresh(document)
        # Store values we need for logging
        doc_id = document.id
        doc_source_id = document.source_id

    logger.info(f"Added document: {doc_id} from {doc_source_id}")

    return document


def add_parsed_many(
    documents: List[Document],
    *,
    dedup_level: Optional[int] = None,
    session: Optional[Any] = None,
) -> List[Document]:
    """Add multiple pre-built documents to the database (low-level helper).

    This function handles parent-child relationships: if a document has
    meta["parent_doc_id"], it will link to the parent's ID. Parents must
    appear before children in the list.

    Args:
        documents: List of Document objects
        dedup_level: Deduplication level (0-3). If None, uses KB_DEDUPLICATION_LEVEL env var or default 1.
                     - 0 (DEDUP_NONE): Always insert
                     - 1 (DEDUP_IDENTITY): Check (source_id, doc_id, parser_id) (DEFAULT)
                     - 2 (DEDUP_HASH): Check content_hash
                     - 3 (DEDUP_IDENTITY_HASH): Check both
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        List of created or updated Document objects

    Raises:
        ValueError: If documents list is empty
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()

    if not documents:
        raise ValueError("Cannot add empty list of documents")

    # Add each document sequentially
    result = []
    # Track doc_id -> database ID mapping for parent-child relationships
    ids = {}  # {doc_id: database_id}
    for doc in documents:
        # If this document references a parent via meta["parent_doc_id"],
        # link it to the parent's database ID (parent must have been added already)
        if "parent_doc_id" in doc.meta:
            parent_id = ids.get(doc.meta["parent_doc_id"])
            if parent_id:
                doc.parent_id = parent_id
        added_doc = add_parsed(doc, dedup_level=dedup_level, session=session)
        # Store this document's ID for potential child documents
        ids[added_doc.doc_id] = added_doc.id
        result.append(added_doc)

    return result


def add_document(
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
    skip_parse: bool = False,
    uri: Optional[str] = None,
    meta: Optional[Dict] = None,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """Add a document to the knowledge base from a file.

    This function provides control over file storage and parsing:
    - Optionally copy files to KB storage (data/sources/{source_id}/)
    - Optionally only create RawDocument without parsing (skip_parse=True)


    Args:
        file_path: Path to the document file to ingest
        source_id: Source identifier (required)
        doc_id: Document ID within the source (required)
        parser_name: Parser to use (e.g., "kb-mcp", "docling"). If None, uses KB_PARSER env var or "kb-mcp".
        extract_images: If True, create separate Document objects for extracted images.
                       If None, reads from PARSE_IMAGE_ADDITIONAL_DOC env var.
        describe_images: If True, generate LLM descriptions for images using vision model.
                        If None, reads from PARSE_IMAGE_LLM_DESCRIPTION env var.
        dedup_level: Deduplication level (0-3). If None, uses KB_DEDUPLICATION_LEVEL env var or default 1.
                     - 0 (DEDUP_NONE): Always insert
                     - 1 (DEDUP_IDENTITY): Check (source_id, doc_id, parser_id) (DEFAULT)
                     - 2 (DEDUP_HASH): Check content_hash
                     - 3 (DEDUP_IDENTITY_HASH): Check both
        force_reparse: If True, re-parse and update even if document already exists (default: False).
        copy_to_kb: If True, copy file to data/sources/{source_id}/{source_id}-{doc_id}.{ext}.
                    The file_path in RawDocument will be updated to the new location.
        skip_parse: If True, only create RawDocument without parsing. Returns early with basic stats.
        uri: Optional URI for the document (e.g., external URL). If not provided and copy_to_kb is False,
             uses file:// URI. If copy_to_kb is True, uses the new file path.
        meta: Optional metadata dictionary to attach to the RawDocument
        session: Optional database session. If provided, uses this session instead of creating a new one.

    Returns:
        Dictionary with statistics:
        - raw_document_id: ID of the RawDocument (UUID string)
        - copied: bool (was file copied to KB storage?)
        - copied_path: str (path where file was copied, if copied=True)
        - parsed: bool (was file parsed?)
        - document_ids: list of Document IDs (if parsed, otherwise empty list)
        - num_documents: int (number of documents created, if parsed)
        - skipped: bool (already exists and not force_reparse?)
        - timing: dict (parsing timing info, if parsed)

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If source_id or doc_id is not provided
        NotImplementedError: If the document type is not supported

    Example:
        ```python
        # Simple document addition without file copying
        result = add_document(
            "/path/to/doc.pdf",
            source_id="arxiv",
            doc_id="2301.12345"
        )
        print(result["num_documents"])

        # Add with file copying to KB storage
        result = add_document(
            "/tmp/download.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
            copy_to_kb=True
        )
        print(result["copied_path"])  # data/sources/arxiv/arxiv-2301.12345.pdf

        # Create RawDocument only, skip parsing
        result = add_document(
            "/path/to/large.pdf",
            source_id="arxiv",
            doc_id="2301.12345",
            skip_parse=True
        )
        print(result["parsed"])  # False
        ```
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()

    # Validate required parameters
    if not source_id:
        raise ValueError("source_id is required")
    if not doc_id:
        raise ValueError("doc_id is required")

    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Initialize result dictionary
    result = {
        "raw_document_id": None,
        "copied": False,
        "copied_path": None,
        "parsed": False,
        "document_ids": [],
        "num_documents": 0,
        "skipped": False,
        "timing": None,
    }

    # Handle file copying if requested
    actual_file_path = file_path
    if copy_to_kb:
        from ...config import get_data_dir

        # Create destination directory: data/sources/{source_id}/
        data_dir = Path(get_data_dir())
        dest_dir = data_dir / "sources" / source_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Create standardized filename: {source_id}-{doc_id}.{ext}
        file_ext = file_path.suffix
        dest_filename = f"{source_id}-{doc_id}{file_ext}"
        dest_path = dest_dir / dest_filename

        # Copy file
        import shutil
        shutil.copy2(file_path, dest_path)
        logger.info(f"Copied file to KB storage: {dest_path}")

        actual_file_path = dest_path
        result["copied"] = True
        result["copied_path"] = str(dest_path)

    # Calculate file hash and metadata
    import hashlib
    import socket
    from ...parser.utils import detect_mime_type

    with open(actual_file_path, "rb") as f:
        file_content = f.read()
        content_hash = hashlib.sha256(file_content).hexdigest()

    file_stat = actual_file_path.stat()
    file_size = file_stat.st_size
    source_type = detect_mime_type(actual_file_path)

    # Prepare URI
    if uri is None:
        uri = f"file://{actual_file_path.absolute()}"

    # Prepare metadata
    raw_meta = dict(meta) if meta else {}
    raw_meta["filename"] = actual_file_path.name

    # Get parser name from parameter or config
    if parser_name is None:
        from ...config import get_parser_config
        parser_name = get_parser_config()['parser']

    # Try to insert RawDocument - returns ID if inserted, None if already exists
    with get_db_session(session) as db_session:
        raw_doc_id = insert_raw_document(
            source_id=source_id,
            doc_id=doc_id,
            file_path=str(actual_file_path.absolute()),
            hostname=socket.gethostname(),
            uri=uri,
            source_type=source_type,
            file_size=file_size,
            content_hash=content_hash,
            meta=raw_meta,
            session=db_session,
        )

        result["raw_document_id"] = raw_doc_id

        # If raw_doc_id is None, file already exists (same content_hash)
        # Always get the existing raw document ID for linking purposes
        if raw_doc_id is None:
            # Get the existing RawDocument to retrieve its ID
            from ..db_models import RawDocument
            existing_raw = db_session.query(RawDocument).filter(
                RawDocument.content_hash == content_hash
            ).first()

            if existing_raw:
                raw_doc_id = existing_raw.id  # Update raw_doc_id for linking
                result["raw_document_id"] = existing_raw.id

                # Check if a Document with this parser already exists
                from ..db_models import Document as DocumentModel
                existing_doc_for_parser = db_session.query(DocumentModel).filter(
                    DocumentModel.raw_document_id == existing_raw.id,
                    DocumentModel.parser_id == parser_name
                ).first()

                # If document with this parser exists and not force_reparse, skip
                if existing_doc_for_parser and not force_reparse:
                    result["skipped"] = True
                    logger.info(
                        f"File already parsed with {parser_name} (hash: {content_hash[:16]}...), "
                        f"skipping (use force_reparse=True to re-parse)"
                    )
                    return result
                elif existing_doc_for_parser and force_reparse:
                    logger.info(
                        f"File already parsed with {parser_name} (hash: {content_hash[:16]}...), "
                        f"but force_reparse=True, re-parsing"
                    )
                elif not existing_doc_for_parser:
                    logger.info(
                        f"RawDocument exists (hash: {content_hash[:16]}...), "
                        f"but no Document for parser {parser_name}, parsing now"
                    )

        # If skip_parse is True, return early (only created RawDocument)
        if skip_parse:
            logger.info(
                f"Created RawDocument for {actual_file_path.name} "
                f"(source_id={source_id}, doc_id={doc_id}), skipping parse"
            )
            return result

        # Get or create parser record
        parser = get_or_create_parser(
            name=parser_name,
            description=f"Parser: {parser_name}",
            session=db_session,
        )

        # Commit to ensure raw_doc and parser have IDs
        db_session.commit()

        # If we get here, we need to parse the file
        # Import parse function (lazy import to avoid circular dependencies)
        from kb_mcp.parser import parse

        # Prepare data for parsing
        parse_data = {
            "source_id": source_id,
            "doc_id": doc_id,
        }
        if meta:
            parse_data["meta"] = meta

        # Parse the file - returns List[dict]
        doc_dicts = parse(
            actual_file_path,
            data=parse_data,
            extract_images=extract_images,
            describe_images=describe_images,
            parser_name=parser_name,
        )

        if not doc_dicts:
            raise ValueError(f"No documents extracted from {actual_file_path}")

        # Extract timing information from first document's meta (if available)
        timing_info = None
        if doc_dicts and isinstance(doc_dicts[0].get("meta"), dict):
            timing_info = doc_dicts[0]["meta"].pop("_parsing_timing", None)
            # Remove filepath and filesize from metadata (internal fields not needed in DB)
            doc_dicts[0]["meta"].pop("filepath", None)

        result["timing"] = timing_info

        # Convert dicts to Document objects
        documents = [Document.from_dict(doc_dict) for doc_dict in doc_dicts]

        # Set raw_document_id and parser_id on all documents
        for doc in documents:
            doc.raw_document_id = raw_doc_id
            doc.parser_id = parser.name

        # Add all documents to the database (handles deduplication via dedup_level)
        added_docs = add_parsed_many(documents, dedup_level=dedup_level, session=db_session)

        # Update result with parsing information
        result["parsed"] = True
        result["document_ids"] = [doc.id for doc in added_docs] if added_docs else []
        result["num_documents"] = len(added_docs) if added_docs else 0

        logger.info(f"Added {len(added_docs) if added_docs else 0} document(s) to database: {result['document_ids']}")

    # Log parsing operation (one entry per file parse, linked to first document)
    if added_docs and session:
        from ..embedding.db_models import ParsingLog

        # Calculate total text length across all extracted documents
        total_text_length = sum(len(doc.text) if doc.text else 0 for doc in added_docs)

        # Create one log entry for the file parse operation
        # Link to first document if available
        first_doc = added_docs[0] if added_docs else None

        # Use timing info from parse() if available, otherwise use 0
        if timing_info:
            text_extraction_time = timing_info.get("text_extraction_time_seconds", 0.0)
            image_description_time = timing_info.get("image_description_time_seconds")
            total_time = timing_info.get("total_time_seconds", text_extraction_time + (image_description_time or 0.0))
        else:
            # Fallback if timing info not available
            text_extraction_time = 0.0
            image_description_time = None
            total_time = 0.0

        log_entry = ParsingLog(
            document_id=first_doc.id if first_doc else None,
            text_extraction_time_seconds=round(text_extraction_time, 3),
            image_description_time_seconds=round(image_description_time, 3) if image_description_time is not None else None,
            total_time_seconds=round(total_time, 3),
            num_documents=len(added_docs),
            text_length=total_text_length,
            hostname=socket.gethostname(),
        )
        session.add(log_entry)

    logger.info(
        f"Ingested {result['num_documents']} document(s) from {actual_file_path.name} "
        f"(source_id={source_id}, doc_id={doc_id})"
    )

    return result


def _get(
    identifier: str | None = None,
    *,
    uuid: str | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
    doc_type: str | None = None,
    filter_dict: Dict[str, Any] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    count_only: bool = False,
    session = None,
) -> Document | list[Document] | int | None:
    """Get document(s) by various criteria.

    Usage:
        ```python
        # UUID (positional, auto-detected)
        doc = get("550e8400-e29b-41d4-a716-446655440000")
        
        # UUID (explicit keyword, guaranteed)
        doc = get(uuid="550e8400-e29b-41d4-a716-446655440000")
        
        # Parse identifier: "source_id_doc_id" (split on "_")
        doc = get("mu2e-docdb_1234-doc1")
        # → source_id="mu2e-docdb", doc_id="1234-doc1"
        
        # Parse identifier: just doc_id (no "_", not UUID)
        doc = get("1234-doc1")
        # → doc_id="1234-doc1"
        
        # Explicit doc_id
        doc = get(doc_id="1234-doc1")
        
        # Explicit source_id and doc_id
        doc = get(source_id="mu2e-docdb", doc_id="1234-doc1")
        
        # Get all documents from a source
        docs = get(source_id="mu2e-docdb")
        
        # Get all documents (empty query)
        docs = get()
        
        # Use filter_dict for advanced filtering
        docs = get(filter_dict={"source_id": "mu2e-docdb", "doc_type": "text"})
        
        # With limit and offset for pagination
        docs = get(source_id="mu2e-docdb", limit=10, offset=20)
        
        # Get count using get_count()
        count = get_count(filter_dict={"source_id": "mu2e-docdb"})
        ```

    Args:
        identifier: Positional argument that can be:
                    - UUID (36 chars with dashes) → used as document UUID
                    - "source_id_doc_id" format → parsed (split on "_")
                    - Otherwise → treated as doc_id
        uuid: Explicit UUID lookup (guaranteed, overrides identifier)
        source_id: Source identifier to filter by
        doc_id: Document ID within source to filter by
        filter_dict: Dictionary with filter criteria. Supported keys:
                    - source_id: Filter by source ID
                    - doc_id: Filter by document ID
                    - doc_type: Filter by document type
                    - source_type: Filter by source type (MIME type)
                    - text_contains: Filter documents containing text (case-insensitive)
        limit: Maximum number of documents to return (ignored if count_only=True)
        offset: Number of documents to skip (ignored if count_only=True)
        count_only: If True, return count instead of documents
        session: Optional database session. If None, creates a new session.

    Returns:
        - If count_only=True: int (count of matching documents)
        - If count_only=False:
          - Single Document if one match found
          - list[Document] if multiple matches found
          - None if no matches found
    """
    _ensure_db_initialized()

    with get_db_session(session) as session:
        query = session.query(Document)

        # Handle explicit uuid parameter (highest priority)
        if uuid:
            query = query.filter(Document.id == uuid)
        elif identifier:
            # Check if identifier is a UUID
            is_uuid = len(identifier) == 36 and identifier.count("-") == 4
            if is_uuid:
                # UUID - use directly
                query = query.filter(Document.id == identifier)
            else:
                # Not a UUID - parse it
                if "_" in identifier:
                    # Split on "_" to get source_id and doc_id
                    parts = identifier.split("_", 1)
                    if len(parts) == 2:
                        parsed_source_id, parsed_doc_id = parts[0], parts[1]
                        # Only use parsed values if explicit parameters not provided
                        if source_id is None:
                            source_id = parsed_source_id
                        if doc_id is None:
                            doc_id = parsed_doc_id
                    else:
                        return None
                else:
                    # No "_" - treat as doc_id if doc_id not explicitly provided
                    if doc_id is None:
                        doc_id = identifier

        # Apply explicit filters (backward compatibility)
        if source_id:
            query = query.filter(Document.source_id == source_id)
        if doc_id:
            query = query.filter(Document.doc_id == doc_id)
        if doc_type:
            query = query.filter(Document.doc_type == doc_type)

        # Apply filter_dict filters (takes precedence if both provided)
        # Also support Elasticsearch-style filter parameter
        es_filter = None
        if filter_dict:
            # Check if filter_dict contains an Elasticsearch-style filter
            if "filter" in filter_dict:
                es_filter = filter_dict["filter"]
            else:
                # Legacy filter_dict format
                if "source_id" in filter_dict and filter_dict["source_id"]:
                    query = query.filter(Document.source_id == filter_dict["source_id"])
                if "doc_id" in filter_dict and filter_dict["doc_id"]:
                    query = query.filter(Document.doc_id == filter_dict["doc_id"])
                if "doc_type" in filter_dict and filter_dict["doc_type"]:
                    query = query.filter(Document.doc_type == filter_dict["doc_type"])
                if "source_type" in filter_dict and filter_dict["source_type"]:
                    query = query.filter(Document.source_type == filter_dict["source_type"])
                if "text_contains" in filter_dict and filter_dict["text_contains"]:
                    query = query.filter(Document.text.contains(filter_dict["text_contains"]))
        
        # Apply Elasticsearch-style filters using the filter helper functions
        if es_filter:
            from ..search.filters import get_filters_fallback
            from sqlalchemy.orm import aliased
            from sqlalchemy import and_

            # Create an alias for Document to use with filter functions
            doc_alias = aliased(Document)

            # Get dialect name
            dialect_name = session.bind.dialect.name if session.bind else None

            # Extract source_id and doc_type from filter_dict if not already set
            filter_source_id = source_id if source_id else (filter_dict.get("source_id") if filter_dict else None)
            filter_doc_type = filter_dict.get("doc_type") if filter_dict else None

            # Extract metadata filters (all keys except known filter keys)
            items_to_iterate = filter_dict.items() if filter_dict else {}
            metadata_kwargs = {k: v for k, v in items_to_iterate
                              if k not in ["source_id", "doc_id", "doc_type", "source_type", "text_contains", "filter"]}

            # Build filters using the same helper as search
            # Note: get_filters_fallback expects an alias, but we can pass Document directly
            # and it will work since we're building conditions on the same model
            filter_conditions = get_filters_fallback(
                Document,  # Pass Document class directly
                source_id=filter_source_id,
                doc_type=filter_doc_type,
                filter=es_filter,
                dialect_name=dialect_name,
                **metadata_kwargs
            )

            # Apply all filter conditions
            if filter_conditions:
                # Combine with existing query filters
                if len(filter_conditions) == 1:
                    query = query.filter(filter_conditions[0])
                else:
                    query = query.filter(and_(*filter_conditions))

        # If count_only, return count early
        if count_only:
            return query.count()

        # Determine ordering field based on date filter if present
        # If filtering by a specific date field, order by that field; otherwise use insert_time
        order_field = Document.insert_time  # Default to insert_time (newest first)
        
        if filter_dict and "filter" in filter_dict:
            es_filter = filter_dict["filter"]
            # Check if filter contains a range query on a date field
            if isinstance(es_filter, dict) and "bool" in es_filter:
                bool_filter = es_filter["bool"]
                if "must" in bool_filter:
                    for condition in bool_filter["must"]:
                        if isinstance(condition, dict) and "range" in condition:
                            range_filter = condition["range"]
                            for field_name in range_filter.keys():
                                if field_name in ["insert_time", "creating_time", "update_time"]:
                                    order_field = getattr(Document, field_name)
                                    break
                            if order_field != Document.insert_time:
                                break
        
        # Apply ordering (newest first - descending order)
        query = query.order_by(order_field.desc())

        # Apply pagination
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        # Execute query
        documents = query.all()

        # Return appropriate type
        if len(documents) == 0:
            return None
        elif len(documents) == 1:
            return documents[0]
        else:
            return documents


def get(
    identifier: str | None = None,
    *,
    uid: str | List[str] | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
    doc_type: str | None = None,
    filter_dict: Dict[str, Any] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    session = None,
) -> Document | list[Document] | None:
    """Get document(s) by various criteria.

    Usage:
        ```python
        # UUID (positional, auto-detected)
        doc = get("550e8400-e29b-41d4-a716-446655440000")

        # UUID (explicit keyword, guaranteed)
        doc = get(uid="550e8400-e29b-41d4-a716-446655440000")

        # Batch UUID retrieval (returns list)
        docs = get(uid=["550e8400-...", "660f9511-...", "770g0622-..."])

        # Parse identifier: "source_id_doc_id" (split on "_")
        doc = get("mu2e-docdb_1234-doc1")
        # → source_id="mu2e-docdb", doc_id="1234-doc1"

        # Parse identifier: just doc_id (no "_", not UUID)
        doc = get("1234-doc1")
        # → doc_id="1234-doc1"

        # Explicit doc_id
        doc = get(doc_id="1234-doc1")

        # Explicit source_id and doc_id
        doc = get(source_id="mu2e-docdb", doc_id="1234-doc1")

        # Get all documents from a source
        docs = get(source_id="mu2e-docdb")

        # Get all documents (empty query)
        docs = get()

        # Use filter_dict for advanced filtering
        docs = get(filter_dict={"source_id": "mu2e-docdb", "doc_type": "text"})

        # With limit and offset for pagination
        docs = get(source_id="mu2e-docdb", limit=10, offset=20)
        ```

    Args:
        identifier: Positional argument that can be:
                    - UUID (36 chars with dashes) → used as document UUID
                    - "source_id_doc_id" format → parsed (split on "_")
                    - Otherwise → treated as doc_id
        uid: Explicit UUID lookup. Can be:
             - Single UUID string → returns single Document
             - List of UUID strings → returns list of Documents (batch retrieval)
        source_id: Source identifier to filter by
        doc_id: Document ID within source to filter by
        filter_dict: Dictionary with filter criteria. Supported keys:
                    - source_id: Filter by source ID
                    - doc_id: Filter by document ID
                    - doc_type: Filter by document type
                    - source_type: Filter by source type (MIME type)
                    - text_contains: Filter documents containing text (case-insensitive)
        limit: Maximum number of documents to return
        offset: Number of documents to skip (for pagination)
        session: Optional database session. If provided, documents remain attached to session.
                If None, creates a new session and detaches documents.

    Returns:
        - Single Document if one match found
        - list[Document] if multiple matches found (or if list of UUIDs provided)
        - None if no matches found
    """
    # Handle batch UUID retrieval
    if isinstance(uid, list):
        # Batch retrieval by list of UUIDs
        with get_db_session(session) as session:
            query = session.query(Document).filter(Document.id.in_(uid))
            docs = query.all()

            # If no session was provided, detach the documents
            if session is None:
                for doc in docs:
                    session.expunge(doc)

            return docs if docs else None

    result = _get(
        identifier=identifier,
        uuid=uid if isinstance(uid, str) else None,
        source_id=source_id,
        doc_id=doc_id,
        doc_type=doc_type,
        filter_dict=filter_dict,
        limit=limit,
        offset=offset,
        count_only=False,
        session=session,
    )
    # Type narrowing: when count_only=False, result is never int
    if isinstance(result, int):
        return None  # Should never happen, but handle for type safety
    return result


def get_count(
    identifier: str | None = None,
    *,
    uuid: str | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
    doc_type: str | None = None,
    filter_dict: Dict[str, Any] | None = None,
) -> int:
    """Get count of documents matching criteria.

    Usage:
        ```python
        # Count all documents
        count = get_count()
        
        # Count documents from a source
        count = get_count(source_id="mu2e-docdb")
        
        # Count with filter_dict
        count = get_count(filter_dict={"doc_type": "text", "source_id": "mu2e-docdb"})
        ```

    Args:
        identifier: Positional argument (same as get())
        uuid: Explicit UUID lookup
        source_id: Source identifier to filter by
        doc_id: Document ID within source to filter by
        filter_dict: Dictionary with filter criteria (same as get())

    Returns:
        Number of documents matching the criteria
    """
    result = _get(
        identifier=identifier,
        uuid=uuid,
        source_id=source_id,
        doc_id=doc_id,
        doc_type=doc_type,
        filter_dict=filter_dict,
        count_only=True,
    )
    # Type narrowing: when count_only=True, result is always int
    return result if isinstance(result, int) else 0


def get_children(
    parent_id: str,
) -> list[Document]:
    """Get child documents of a parent document.
    
    Usage:
        ```python
        children = get_children("550e8400-e29b-41d4-a716-446655440000")
        ```
    
    Args:
        parent_id: UUID of the parent document
    
    Returns:
        List of child Document objects (empty list if no children found)
    """
    _ensure_db_initialized()
    
    with get_db_session() as session:
        query = session.query(Document).filter(Document.parent_id == parent_id)
        
        # Apply ordering (default: by insert_time descending)
        query = query.order_by(Document.insert_time.desc())
        
        # Execute query
        documents = query.all()
        
        # Detach all results from session
        for doc in documents:
            session.expunge(doc)
    
    return documents


def add_source(
    source_id: str,
    name: str | None = None,
    description: str | None = None,
    base_uri: str | None = None,
    meta: Dict[str, Any] | None = None,
    session: Optional[Any] = None,
) -> Source:
    """Add or update a source.

    Usage:
        ```python
        source = add_source(
            source_id="mu2e-docdb",
            name="Mu2e DocDB",
            description="Mu2e Document Database",
            base_uri="https://mu2e-docdb.fnal.gov/docdb",
            meta={"type": "docdb", "project": "mu2e"},
        )
        ```

    Args:
        source_id: Unique identifier for the source (primary key)
        name: Human-readable name for the source
        description: Description of the source
        base_uri: Base URI for this source
        meta: Additional metadata as dictionary
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        Source object (created or updated)
    """
    _ensure_db_initialized()

    with get_db_session(session) as session:
        # Use merge to create or update (upsert pattern)
        source = Source(
            id=source_id,
            name=name,
            description=description,
            base_uri=base_uri,
            meta=meta or {},
        )
        source = session.merge(source)
        session.flush()  # Ensure the merge is persisted before refresh
        # Refresh to ensure all attributes are loaded
        session.refresh(source)

    logger.info(f"Added/updated source: {source_id}")
    return source


def get_or_create_parser(
    name: str,
    description: str | None = None,
    meta: Dict[str, Any] | None = None,
    session: Optional[Any] = None,
) -> "Parser":
    """Get or create a parser by name.

    Args:
        name: Parser framework name (e.g., "kb-mcp", "docling", "adaParse", "manual")
        description: Description of the parser framework
        meta: Additional metadata (URL, capabilities, etc.)
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        Parser object (created or retrieved)
    """
    from ..db_models import Parser

    _ensure_db_initialized()

    with get_db_session(session) as session:
        # Query first to see if parser exists
        parser = session.query(Parser).filter(Parser.name == name).first()
        
        if parser:
            # Update existing parser if fields are provided
            if description is not None:
                parser.description = description
            if meta is not None:
                parser.meta = meta or {}
        else:
            # Create new parser
            parser = Parser(
                name=name,
                description=description,
                meta=meta or {},
            )
            session.add(parser)
            session.flush()  # Flush to get the ID
        
        # Refresh to ensure all attributes are loaded
        session.refresh(parser)

    logger.info(f"Added/updated parser: {name}")
    return parser


def get_or_create_raw_document(
    source_id: str,
    doc_id: str,
    file_path: str | None = None,
    hostname: str | None = None,
    uri: str | None = None,
    source_type: str | None = None,
    file_size: int | None = None,
    content_hash: str | None = None,
    meta: Dict[str, Any] | None = None,
    session: Optional[Any] = None,
) -> "RawDocument":
    """Get or create a raw document record.

    If a RawDocument with the same content_hash exists, returns it.
    Otherwise, creates a new RawDocument.

    Args:
        source_id: Source identifier
        doc_id: Document identifier within the source
        file_path: Local filesystem path (e.g., "/data/files/doc.pdf")
        hostname: Hostname where file_path is valid (e.g., "compute-node-01")
        uri: Original source URI (typically a URL)
        source_type: MIME type (e.g., "application/pdf")
        file_size: File size in bytes
        content_hash: Hash of file content (required for deduplication)
        meta: Additional metadata
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        RawDocument object (created or retrieved)

    Raises:
        ValueError: If content_hash is not provided
    """
    from ..db_models import RawDocument

    _ensure_db_initialized()

    if not content_hash:
        raise ValueError("content_hash is required for raw document deduplication")

    with get_db_session(session) as session:
        # Check if raw document with same hash already exists
        existing = session.query(RawDocument).filter(
            RawDocument.content_hash == content_hash
        ).first()

        if existing:
            logger.info(f"Found existing raw document with hash: {content_hash[:16]}...")
            return existing

        # Create new raw document
        raw_doc = RawDocument(
            source_id=source_id,
            doc_id=doc_id,
            file_path=file_path,
            hostname=hostname,
            uri=uri,
            source_type=source_type or "application/octet-stream",
            file_size=file_size,
            content_hash=content_hash,
            meta=meta or {},
        )
        session.add(raw_doc)
        session.flush()  # Get the ID without committing
        session.refresh(raw_doc)

        logger.info(f"Created raw document: {raw_doc.id} (hash: {content_hash[:16]}...)")
        return raw_doc


def insert_raw_document(
    source_id: str,
    doc_id: str,
    file_path: str | None = None,
    hostname: str | None = None,
    uri: str | None = None,
    source_type: str | None = None,
    file_size: int | None = None,
    content_hash: str | None = None,
    meta: Dict[str, Any] | None = None,
    session: Optional[Any] = None,
) -> str | None:
    """Insert a raw document record if it doesn't already exist (by content_hash).

    Args:
        source_id: Source identifier
        doc_id: Document identifier within the source
        file_path: Local filesystem path (e.g., "/data/files/doc.pdf")
        hostname: Hostname where file_path is valid (e.g., "compute-node-01")
        uri: Original source URI (typically a URL)
        source_type: MIME type (e.g., "application/pdf")
        file_size: File size in bytes
        content_hash: Hash of file content (required for deduplication)
        meta: Additional metadata
        session: Optional database session. If provided, uses this session instead of creating a new one.
                When a session is provided, the caller is responsible for committing the transaction.

    Returns:
        RawDocument ID if inserted, None if already exists

    Raises:
        ValueError: If content_hash is not provided
    """
    from ..db_models import RawDocument

    _ensure_db_initialized()

    if not content_hash:
        raise ValueError("content_hash is required for raw document deduplication")

    with get_db_session(session) as session:
        # Check if raw document with same hash already exists
        existing = session.query(RawDocument).filter(
            RawDocument.content_hash == content_hash
        ).first()

        if existing:
            logger.info(f"Raw document already exists with hash: {content_hash[:16]}...")
            return None

        # Create new raw document
        raw_doc = RawDocument(
            source_id=source_id,
            doc_id=doc_id,
            file_path=file_path,
            hostname=hostname,
            uri=uri,
            source_type=source_type or "application/octet-stream",
            file_size=file_size,
            content_hash=content_hash,
            meta=meta or {},
        )
        session.add(raw_doc)
        session.flush()  # Get the ID without committing
        session.refresh(raw_doc)

        logger.info(f"Inserted raw document: {raw_doc.id} (hash: {content_hash[:16]}...)")
        return raw_doc.id


def delete_document(
    document_id: str,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """Delete a document and return information about what was deleted.
    
    Args:
        document_id: UUID of the document to delete
        session: Optional database session (creates new one if not provided)
    
    Returns:
        Dictionary with:
        - deleted: bool - Whether the document was deleted
        - document_id: str - The document ID
        - chunk_count: int - Number of chunks that were cascade deleted (if available)
    
    Raises:
        ValueError: If document not found
    """
    with get_db_session(session) as session:
        document = session.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise ValueError(f"Document {document_id} not found")

        # Get chunk count before deletion
        from ..embedding import get_chunks
        chunks = get_chunks(document_id=document_id)
        chunk_count = len(chunks) if chunks else 0

        # Manually delete chunks first to avoid foreign key constraint issues
        from ..embedding.db_models import Chunk, ChunkEmbeddingLog
        session.query(Chunk).filter(Chunk.document_id == document_id).delete(synchronize_session=False)
        session.flush()
        
        # Manually delete chunk embedding logs to avoid foreign key constraint issues
        session.query(ChunkEmbeddingLog).filter(ChunkEmbeddingLog.document_id == document_id).delete(synchronize_session=False)
        session.flush()

        # Delete the document (embeddings will be cascade deleted via chunks)
        session.delete(document)
        session.commit()

        return {
            "deleted": True,
            "document_id": document_id,
            "chunk_count": chunk_count,
        }


def get_raw_document(
    document_id: str,
    session: Optional[Any] = None,
) -> Optional[Any]:
    """Get a RawDocument by Document ID.
    
    This function looks up a Document by its ID, then retrieves the associated RawDocument
    using the Document's raw_document_id field.
    
    Args:
        document_id: UUID of the Document
        session: Optional database session (creates new one if not provided)
    
    Returns:
        RawDocument object if found, None if Document not found or has no raw_document_id
    
    Example:
        ```python
        from kb_mcp.kb.documents import get_raw_document
        
        # Get raw document for a document
        raw_doc = get_raw_document("550e8400-e29b-41d4-a716-446655440000")
        if raw_doc:
            print(f"Raw document: {raw_doc.id}")
            print(f"File path: {raw_doc.file_path}")
        else:
            print("No raw document found for this document")
        ```
    """
    from ..db_models import RawDocument, Document
    
    with get_db_session(session) as session:
        # Get the document
        document = session.query(Document).filter(Document.id == document_id).first()
        if not document:
            return None
        
        # Check if document has a raw_document_id
        if not document.raw_document_id:
            return None
        
        # Get the raw document
        raw_doc = session.query(RawDocument).filter(
            RawDocument.id == document.raw_document_id
        ).first()
        
        return raw_doc


def delete_raw_document(
    raw_document_id: str,
    delete_linked_documents: bool = False,
    session: Optional[Any] = None,
) -> Dict[str, Any]:
    """Delete a raw document and return information about what was affected.
    
    By default, deleting a RawDocument will set raw_document_id to NULL in related Documents,
    but will not delete the Documents themselves. If delete_linked_documents=True, all linked
    documents will also be deleted (along with their chunks and embeddings).
    
    Args:
        raw_document_id: UUID of the raw document to delete
        delete_linked_documents: If True, also delete all documents linked to this raw document
        session: Optional database session (creates new one if not provided)
    
    Returns:
        Dictionary with:
        - deleted: bool - Whether the raw document was deleted
        - raw_document_id: str - The raw document ID
        - document_count: int - Number of documents that referenced this raw document
        - deleted_documents: int - Number of documents deleted (if delete_linked_documents=True)
    
    Raises:
        ValueError: If raw document not found
    """
    from ..db_models import RawDocument, Document
    from ..embedding.db_models import Chunk, ChunkEmbeddingLog
    
    with get_db_session(session) as session:
        raw_doc = session.query(RawDocument).filter(RawDocument.id == raw_document_id).first()
        if not raw_doc:
            raise ValueError(f"RawDocument {raw_document_id} not found")

        # Get documents that reference this raw document
        linked_documents = session.query(Document).filter(
            Document.raw_document_id == raw_document_id
        ).all()
        document_count = len(linked_documents)
        
        deleted_documents = 0
        
        # If requested, delete all linked documents first
        if delete_linked_documents and linked_documents:
            for doc in linked_documents:
                # Manually delete chunks first
                session.query(Chunk).filter(Chunk.document_id == doc.id).delete(synchronize_session=False)
                session.flush()
                
                # Manually delete chunk embedding logs
                session.query(ChunkEmbeddingLog).filter(ChunkEmbeddingLog.document_id == doc.id).delete(synchronize_session=False)
                session.flush()
                
                # Delete the document
                session.delete(doc)
                deleted_documents += 1
            
            session.flush()

        # Delete the raw document
        session.delete(raw_doc)
        session.commit()

        result = {
            "deleted": True,
            "raw_document_id": raw_document_id,
            "document_count": document_count,
        }
        
        if delete_linked_documents:
            result["deleted_documents"] = deleted_documents
        
        return result


def get_options() -> Dict[str, Any]:
    """Get filter options for the knowledge base.

    Returns a dictionary with available filter options that can be used
    in filter_dict or for building UI dropdowns.

    Returns:
        Dictionary with:
        - source_options: List of dicts with {"id": str, "name": str | None, "count": int}
        - doc_type_options: List of dicts with {"doc_type": str, "count": int}
    """
    _ensure_db_initialized()

    from sqlalchemy import func

    with get_db_session() as session:
        # Get sources with document counts
        source_counts = (
            session.query(
                Source.id,
                Source.name,
                func.count(Document.id).label("count")
            )
            .outerjoin(Document, Source.id == Document.source_id)
            .group_by(Source.id, Source.name)
            .order_by(Source.id)
            .all()
        )

        source_options = [
            {
                "id": source_id,
                "name": source_name,
                "count": count
            }
            for source_id, source_name, count in source_counts
        ]

        # Get document types with counts
        doc_type_counts = (
            session.query(
                Document.doc_type,
                func.count(Document.id).label("count")
            )
            .group_by(Document.doc_type)
            .order_by(Document.doc_type)
            .all()
        )

        doc_type_options = [
            {
                "doc_type": doc_type,
                "count": count
            }
            for doc_type, count in doc_type_counts
        ]

    return {
        "source_options": source_options,
        "doc_type_options": doc_type_options,
    }



