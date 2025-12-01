"""Knowledge base operations - simple get and add functions."""

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .database import get_db_session, init_db
from .core import Document, Source

logger = logging.getLogger(__name__)

# Track if database has been initialized
_db_initialized = False


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

    Args:
        data: Document object or dictionary with document data

    Returns:
        Created Document object

    Raises:
        ValueError: If data is not Document or dict, or if both text and binary are None
    """
    # Ensure database is initialized (lazy loading)
    _ensure_db_initialized()

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
) -> List[Document]:
    """Add multiple documents to the database.
    
    Usage:
        from test_mcp.kb import add_many
        
        docs = add_many([doc1, doc2, doc3])

    Args:
        documents: List of Document objects

    Returns:
        List of created Document objects

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
        added_doc = add(doc)
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
    result = add_many(documents)
    
    final_source_id = parse_data["source_id"]
    final_doc_id = parse_data["doc_id"]
    logger.info(
        f"Added {len(result)} document(s) from {file_path.name} "
        f"(source_id={final_source_id}, doc_id={final_doc_id})"
    )
    
    return result


def get(
    identifier: str | None = None,
    *,
    uuid: str | None = None,
    source_id: str | None = None,
    doc_id: str | None = None,
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

    Args:
        identifier: Positional argument that can be:
                    - UUID (36 chars with dashes) → used as document UUID
                    - "source_id_doc_id" format → parsed (split on "_")
                    - Otherwise → treated as doc_id
        uuid: Explicit UUID lookup (guaranteed, overrides identifier)
        source_id: Source identifier to filter by
        doc_id: Document ID within source to filter by

    Returns:
        - Single Document if one match found
        - list[Document] if multiple matches found
        - None if no matches found
    """
    _ensure_db_initialized()

    with get_db_session() as session:
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

        # Apply filters
        if source_id:
            query = query.filter(Document.source_id == source_id)
        if doc_id:
            query = query.filter(Document.doc_id == doc_id)

        # Execute query
        documents = query.all()

        # Detach all results from session
        for doc in documents:
            session.expunge(doc)

    # Return appropriate type
    if len(documents) == 0:
        return None
    elif len(documents) == 1:
        return documents[0]
    else:
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



