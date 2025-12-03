"""Knowledge base operations - simple get and add functions."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .database import get_db_session, init_db
from .core import Document, Source

logger = logging.getLogger(__name__)

# Track if database has been initialized
_db_initialized = False

# Deduplication levels
DEDUP_LEVEL_INSERT = 0  # Insert duplicates, no warnings
DEDUP_LEVEL_WARN = 1  # Insert with warnings
DEDUP_LEVEL_OVERWRITE_HASH = 2  # Overwrite if same hash
DEDUP_LEVEL_OVERWRITE_HASH_WARN = 3  # Same as 2 but with warnings for existing source_id, doc_id (default)
DEDUP_LEVEL_OVERWRITE_ALL = 4  # Overwrite same hash and same source_id, doc_id


def _get_default_dedup_level() -> int:
    """Get default deduplication level from environment variable.
    
    Returns:
        Deduplication level (0-4), default is 2
    """
    level_str = os.getenv("KB_DEDUPLICATION_LEVEL", "3")
    return int(level_str)


def _find_duplicates(
    document: Document,
    session: Any,
) -> Tuple[Optional[Document], Optional[Document]]:
    """Find duplicate documents by source_id+doc_id and by content_hash.
    
    Args:
        document: Document to check for duplicates
        session: Database session
    
    Returns:
        Tuple of (existing_by_id, existing_by_hash):
        - existing_by_id: Document with same source_id and doc_id, or None
        - existing_by_hash: Document with same content_hash, or None
    """
    existing_by_id = None
    existing_by_hash = None
    
    # Check for existing document with same source_id and doc_id
    if document.source_id and document.doc_id:
        existing_by_id = (
            session.query(Document)
            .filter(
                Document.source_id == document.source_id,
                Document.doc_id == document.doc_id,
            )
            .first()
        )
    
    # Check for existing document with same content_hash
    if document.content_hash:
        existing_by_hash = (
            session.query(Document)
            .filter(Document.content_hash == document.content_hash)
            .first()
        )
    
    return existing_by_id, existing_by_hash


def _update_document(existing: Document, new: Document, session: Any, update_hash: bool = False) -> Document:
    """Update existing document with new document's data.
    
    Args:
        existing: Existing document to update
        new: New document with updated data
        session: Database session
        update_hash: If True, also update content_hash; otherwise keep existing hash
    
    Returns:
        Updated document (detached from session)
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
    session.commit()
    session.refresh(existing)
    session.expunge(existing)
    return existing


def _handle_duplicate(
    document: Document,
    existing_by_id: Optional[Document],
    existing_by_hash: Optional[Document],
    dedup_level: int,
    session: Any,
) -> Optional[Document]:
    """Handle duplicate document based on deduplication level.
    
    Args:
        document: New document to add
        existing_by_id: Existing document with same source_id+doc_id, or None
        existing_by_hash: Existing document with same content_hash, or None
        dedup_level: Deduplication level (0-4)
        session: Database session
    
    Returns:
        Document object (existing or new), or None if should insert new
    """
    # Level 0: Insert duplicates, no warnings
    if dedup_level == DEDUP_LEVEL_INSERT:
        return None
    
    # Level 1: Insert with warnings
    if dedup_level == DEDUP_LEVEL_WARN:
        if existing_by_id:
            logger.warning(
                f"Duplicate source_id+doc_id: {document.source_id}/{document.doc_id} "
                f"(existing: {existing_by_id.id})"
            )
        if existing_by_hash and existing_by_hash.id != (existing_by_id.id if existing_by_id else None):
            logger.warning(
                f"Duplicate content_hash: {document.content_hash} "
                f"(existing: {existing_by_hash.id})"
            )
        return None
    
    # Level 2: Overwrite if same hash
    if dedup_level == DEDUP_LEVEL_OVERWRITE_HASH:
        if existing_by_hash:
            updated = _update_document(existing_by_hash, document, session, update_hash=False)
            logger.warning(
                f"Replaced document {updated.id} (same content_hash: {document.content_hash[:16] if document.content_hash else 'N/A'}...)"
            )
            return updated
        return None
    
    # Level 3: Same as 2 but with warnings for existing source_id, doc_id
    if dedup_level == DEDUP_LEVEL_OVERWRITE_HASH_WARN:
        if existing_by_id and (not existing_by_hash or existing_by_id.id != existing_by_hash.id):
            logger.warning(
                f"Existing source_id+doc_id: {document.source_id}/{document.doc_id} "
                f"(existing: {existing_by_id.id}), but different content_hash"
            )
        if existing_by_hash:
            updated = _update_document(existing_by_hash, document, session, update_hash=False)
            logger.warning(
                f"Replaced document {updated.id} (same content_hash: {document.content_hash[:16] if document.content_hash else 'N/A'}...)"
            )
            return updated
        return None
    
    # Level 4: Overwrite same hash and same source_id, doc_id
    if dedup_level == DEDUP_LEVEL_OVERWRITE_ALL:
        if existing_by_id:
            updated = _update_document(existing_by_id, document, session, update_hash=True)
            logger.warning(
                f"Replaced document {updated.id} "
                f"(same source_id+doc_id: {document.source_id}/{document.doc_id})"
            )
            return updated
        if existing_by_hash:
            updated = _update_document(existing_by_hash, document, session, update_hash=False)
            logger.warning(
                f"Replaced document {updated.id} (same content_hash: {document.content_hash[:16] if document.content_hash else 'N/A'}...)"
            )
            return updated
        return None
    
    # Unknown level - default to insert
    logger.warning(f"Unknown deduplication level {dedup_level}, proceeding with insert")
    return None


def _ensure_db_initialized() -> None:
    """Ensure database is initialized (lazy loading)."""
    global _db_initialized
    if not _db_initialized:
        init_db(create_tables=True)
        _db_initialized = True


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


def add(
    data: Union[Document, Dict[str, Any]],
    *,
    dedup_level: Optional[int] = None,
) -> Document:
    """Add a new document to the database.

    Usage:
        # Add from dict (recommended)
        doc = add({
            "source_id": "mu2e-docdb",
            "source_type": "application/pdf",
            "text": "Content...",
        })
        
        # Add Document object
        doc_obj = Document.from_dict({...})
        doc = add(doc_obj)
        
        # With custom deduplication level
        doc = add(data, dedup_level=4)

    Args:
        data: Document object or dictionary with document data
        dedup_level: Deduplication level (0-4). If None, uses KB_DEDUPLICATION_LEVEL env var or default 2.
                     - 0: Insert duplicates, no warnings
                     - 1: Insert with warnings
                     - 2: Overwrite if same hash (default)
                     - 3: Same as 2 but with warnings for existing source_id, doc_id
                     - 4: Overwrite same hash and same source_id, doc_id

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

    # Always compute content hash
    _compute_hash(document)

    # Save to database
    with get_db_session() as session:
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
            document, existing_by_id, existing_by_hash, dedup_level, session
        )
        
        if existing_doc is not None:
            # Document was updated, return it
            return existing_doc
        
        # No duplicate or level 0/1 - proceed with insert
        session.add(document)
        session.flush()  # Get the ID
        session.commit()
        # Refresh to ensure all attributes are loaded
        session.refresh(document)
        # Store values we need for logging before expunging
        doc_id = document.id
        doc_source_id = document.source_id
        # Expunge to detach from session (prevents DetachedInstanceError)
        session.expunge(document)

    logger.info(f"Added document: {doc_id} from {doc_source_id}")

    return document


def add_many(
    documents: List[Document],
    *,
    dedup_level: Optional[int] = None,
) -> List[Document]:
    """Add multiple documents to the database.
    
    Usage:
        from test_mcp.kb import add_many
        
        docs = add_many([doc1, doc2, doc3])
        
        # With custom deduplication level
        docs = add_many([doc1, doc2, doc3], dedup_level=4)

    Args:
        documents: List of Document objects
        dedup_level: Deduplication level (0-4). If None, uses KB_DEDUPLICATION_LEVEL env var or default 2.
                     - 0: Insert duplicates, no warnings
                     - 1: Insert with warnings
                     - 2: Overwrite if same hash (default)
                     - 3: Same as 2 but with warnings for existing source_id, doc_id
                     - 4: Overwrite same hash and same source_id, doc_id

    Returns:
        List of created or updated Document objects

    Raises:
        ValueError: If documents list is empty
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()

    if not documents:
        raise ValueError("Cannot add empty list of documents")
    
    # Add each document
    result = []
    # id mapping for parent-child relationships
    ids = {} # doc_id -> id
    for doc in documents:
        if "parent_doc_id" in doc.meta:
            parent_id = ids.get(doc.meta["parent_doc_id"])
            if parent_id:
                doc.parent_id = parent_id
        added_doc = add(doc, dedup_level=dedup_level)
        ids[added_doc.doc_id] = added_doc.id
        result.append(added_doc)

    return result


def add_from_path(
    file_path: Union[str, Path],
    *,
    data: Optional[Dict[str, Any]] = None,
    source_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    parse_image_additional_doc: Optional[bool] = None,
    parse_image_llm_description: Optional[bool] = None,
    dedup_level: Optional[int] = None,
) -> List[Document]:
    """Parse a file and add extracted document(s) to the knowledge base.
    
    This function parses a document file, extracts text and optionally images,
    then adds all resulting documents to the knowledge base.
    
    Usage:
        from test_mcp.kb import add_from_path
        
        # Using data dict (recommended for complex cases)
        docs = add_from_path(
            "document.pdf",
            data={
                "source_id": "mu2e-docdb",
                "doc_id": "1234",
                "meta": {"author": "John Doe"}
            }
        )
        
        # Using individual parameters (simpler for basic cases)
        docs = add_from_path(
            "document.pdf",
            source_id="mu2e-docdb"
        )
        
        # With explicit doc_id
        docs = add_from_path(
            "document.pdf",
            source_id="mu2e-docdb",
            doc_id="1234"
        )
        
        # With image extraction
        docs = add_from_path(
            "document.pdf",
            data={"source_id": "mu2e-docdb", "doc_id": "1234"},
            parse_image_additional_doc=True,
            parse_image_llm_description=True
        )
    
    Args:
        file_path: Path to the document file to parse
        data: Optional dictionary with document fields (same as Document.from_dict).
              If provided, must include source_id. Can include doc_id, meta, source_type, etc.
              If not provided, source_id must be passed as a separate parameter.
        source_id: Source identifier. Required if data is not provided.
                   If both data and source_id are provided, source_id overrides data['source_id'].
        doc_id: Document ID within the source. If not provided:
                - Uses data['doc_id'] if data is provided
                - Otherwise uses filename stem
        parse_image_additional_doc: If True, create separate Document objects for images.
                                    If None, reads from PARSE_IMAGE_ADDITIONAL_DOC env var.
        parse_image_llm_description: If True, generate LLM descriptions for images.
                                     If None, reads from PARSE_IMAGE_LLM_DESCRIPTION env var.
        dedup_level: Deduplication level (0-4). If None, uses KB_DEDUPLICATION_LEVEL env var or default 2.
                     - 0: Insert duplicates, no warnings
                     - 1: Insert with warnings
                     - 2: Overwrite if same hash (default)
                     - 3: Same as 2 but with warnings for existing source_id, doc_id
                     - 4: Overwrite same hash and same source_id, doc_id
    
    Returns:
        List of created Document objects:
        - First: Main document with extracted text
        - Rest: Image documents (if parse_image_additional_doc=True)
    
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If source_id is not provided (neither in data nor as parameter)
        NotImplementedError: If the document type is not supported
    
    Example:
        from test_mcp.kb import add_from_path
        
        # Using data dict with metadata
        documents = add_from_path(
            "/path/to/document.pdf",
            data={
                "source_id": "local",
                "doc_id": "my-doc-123",
                "meta": {"category": "research", "year": 2024}
            }
        )
        
        # Main document is documents[0]
        # If images were extracted, they're in documents[1:]
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()
    
    file_path = Path(file_path)
    
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    
    # Import parse function (lazy import to avoid circular dependencies)
    try:
        from ..parser import parse
    except ImportError:
        raise ImportError(
            "Parser module not available. Install with: pip install -e '.[parser]'"
        )
    
    # Prepare data dict for parse()
    # Start with provided data dict or empty dict
    parse_data = dict(data) if data else {}
    
    # Override with individual parameters if provided
    if source_id is not None:
        parse_data["source_id"] = source_id
    if doc_id is not None:
        parse_data["doc_id"] = doc_id
    
    if "source_id" not in parse_data:
        parse_data["source_id"] = "local"
    
    # Set doc_id if not provided
    if "doc_id" not in parse_data:
        parse_data["doc_id"] = file_path.stem
    
    # Parse the file - returns List[dict]
    doc_dicts = parse(
        file_path,
        data=parse_data,
        parse_image_additional_doc=parse_image_additional_doc,
        parse_image_llm_description=parse_image_llm_description,
    )
    
    if not doc_dicts:
        raise ValueError(f"No documents extracted from {file_path}")
    
    # Convert dicts to Document objects
    documents = [Document.from_dict(doc_dict) for doc_dict in doc_dicts]
    
    # Add all documents to the database
    result = add_many(documents, dedup_level=dedup_level)
    
    final_source_id = parse_data["source_id"]
    final_doc_id = parse_data["doc_id"]
    logger.info(
        f"Added {len(result)} document(s) from {file_path.name} "
        f"(source_id={final_source_id}, doc_id={final_doc_id})"
    )
    
    return result


def _get(
    identifier: str | None = None,
    *,
    uuid: str | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
    filter_dict: Dict[str, Any] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    count_only: bool = False,
    session = None,
) -> Document | list[Document] | int | None:
    """Get document(s) by various criteria.

    Usage:
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

    # Determine if we need to create our own session
    own_session = session is None

    if own_session:
        session = get_db_session().__enter__()

    try:
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

        # Apply filter_dict filters (takes precedence if both provided)
        if filter_dict:
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

        # If count_only, return count early
        if count_only:
            return query.count()

        # Apply ordering (default: by insert_time descending)
        query = query.order_by(Document.insert_time.desc())

        # Apply pagination
        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        # Execute query
        documents = query.all()

        # Only detach from session if we created it
        if own_session:
            for doc in documents:
                session.expunge(doc)

        # Return appropriate type
        if len(documents) == 0:
            return None
        elif len(documents) == 1:
            return documents[0]
        else:
            return documents

    finally:
        # Close session if we created it
        if own_session:
            session.close()


def get(
    identifier: str | None = None,
    *,
    uuid: str | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
    filter_dict: Dict[str, Any] | None = None,
    limit: int | None = None,
    offset: int | None = None,
    session = None,
) -> Document | list[Document] | None:
    """Get document(s) by various criteria.

    Usage:
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
        limit: Maximum number of documents to return
        offset: Number of documents to skip (for pagination)
        session: Optional database session. If provided, documents remain attached to session.
                If None, creates a new session and detaches documents.

    Returns:
        - Single Document if one match found
        - list[Document] if multiple matches found
        - None if no matches found
    """
    result = _get(
        identifier=identifier,
        uuid=uuid,
        source_id=source_id,
        doc_id=doc_id,
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
    filter_dict: Dict[str, Any] | None = None,
) -> int:
    """Get count of documents matching criteria.

    Usage:
        # Count all documents
        count = get_count()
        
        # Count documents from a source
        count = get_count(source_id="mu2e-docdb")
        
        # Count with filter_dict
        count = get_count(filter_dict={"doc_type": "text", "source_id": "mu2e-docdb"})

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
        children = get_children("550e8400-e29b-41d4-a716-446655440000")
    
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
) -> Source:
    """Add or update a source.

    Usage:
        source = add_source(
            source_id="mu2e-docdb",
            name="Mu2e DocDB",
            description="Mu2e Document Database",
            base_uri="https://mu2e-docdb.fnal.gov/docdb",
            meta={"type": "docdb", "project": "mu2e"},
        )

    Args:
        source_id: Unique identifier for the source (primary key)
        name: Human-readable name for the source
        description: Description of the source
        base_uri: Base URI for this source
        meta: Additional metadata as dictionary

    Returns:
        Source object (created or updated)
    """
    _ensure_db_initialized()

    with get_db_session() as session:
        # Use merge to create or update (upsert pattern)
        source = Source(
            id=source_id,
            name=name,
            description=description,
            base_uri=base_uri,
            meta=meta or {},
        )
        source = session.merge(source)
        session.commit()
        # Refresh to ensure all attributes are loaded
        session.refresh(source)
        # Expunge to detach from session (prevents DetachedInstanceError)
        session.expunge(source)

    logger.info(f"Added/updated source: {source_id}")
    return source




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
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()
    
    try:
        document = session.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise ValueError(f"Document {document_id} not found")
        
        # Get chunk count before deletion (if embedding module available)
        chunk_count = 0
        try:
            from .embedding import get_chunks
            chunks = get_chunks(document_id=document_id)
            chunk_count = len(chunks) if chunks else 0
        except (ImportError, Exception):
            pass
        
        # Delete the document (chunks and embeddings will be cascade deleted)
        session.delete(document)
        
        if own_session:
            session.commit()
        
        return {
            "deleted": True,
            "document_id": document_id,
            "chunk_count": chunk_count,
        }
    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


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


