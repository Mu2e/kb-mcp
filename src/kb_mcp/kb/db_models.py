"""Core knowledge base models."""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    TypeDecorator,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.inspection import inspect as sqlalchemy_inspect

Base = declarative_base()


class JSONB(TypeDecorator):
    """JSONB type for PostgreSQL, JSON for SQLite.
    
    This type uses PostgreSQL's JSONB type for better performance and indexing,
    but falls back to JSON for SQLite compatibility.
    """
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(postgresql.JSONB)
        else:
            return dialect.type_descriptor(JSON)


class Parser(Base):
    """Table 'parsers' for tracking parser frameworks.

    This table tracks which parser framework was used to process documents.
    Implementation details (e.g., pypdf vs pdfplumber) are stored in document.meta
    for debugging purposes.

    Attributes:
        name (str): Parser framework name (e.g., "kb-mcp", "docling", "adaParse", "manual").
                   This is the primary key.
        description (str): Description of the parser framework.
        meta (dict): Additional metadata (URL, capabilities, etc.).
        created_time (datetime): Timestamp when the parser was registered.
    """
    __tablename__ = "parsers"

    # Use name as primary key for simplicity
    name = Column(String(128), primary_key=True)
    description = Column(Text, nullable=True)
    meta = Column(JSONB, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )

    # Relationships
    documents = relationship("Document", back_populates="parser")

    def __repr__(self) -> str:
        return f"<Parser(name={self.name})>"


class RawDocument(Base):
    """Table 'documents_raw' for managing raw document files.

    This table tracks the original files before processing. It does not store
    the actual file binary data - only references to where files are stored.

    Attributes:
        id (str): Primary key (UUID stored as string).
        source_id (str): Foreign key to the sources table.
        doc_id (str): Human-readable document identifier within the source.
        file_path (str): Local filesystem path where file is stored (e.g., "/data/files/doc.pdf").
        hostname (str): Hostname where the file_path is valid (e.g., "compute-node-01").
        uri (str): Original source URI where the document was obtained (typically a URL).
        source_type (str): MIME type of the file (e.g., "application/pdf").
        file_size (int): File size in bytes.
        content_hash (str): Hash of file content for deduplication.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the raw document was created.
        updated_time (datetime): Timestamp when the raw document was last updated.
    """
    __tablename__ = "documents_raw"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    source_id = Column(
        String(256),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    doc_id = Column(String(512), nullable=True, index=True)
    file_path = Column(String(2048), nullable=True)
    hostname = Column(String(256), nullable=True, index=True)
    uri = Column(String(2048), nullable=True, index=True)
    source_type = Column(String(128), nullable=False, index=True)
    file_size = Column(Integer, nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    meta = Column(JSONB, nullable=True, default=dict)

    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    # Relationships
    source_ref = relationship("Source", back_populates="raw_documents")
    documents = relationship("Document", back_populates="raw_document")

    # Indexes
    __table_args__ = (
        Index("idx_documents_raw_content_hash", "content_hash"),
        Index("idx_documents_raw_source_doc_id", "source_id", "doc_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<RawDocument(id={self.id}, source_id={self.source_id}, "
            f"doc_id={self.doc_id}, uri={self.uri})>"
        )


class Source(Base):
    """Table 'sources' for document source information.

    Attributes:
        id (str): Primary key (UUID stored as string). Works with both PostgreSQL and SQLite.
        name (str): Human-readable name. For example 'inspre-hep' or 'mu2e-docdb'.
        description (str): Description of the source.
        base_uri (str): Base URI for this source.
        meta (dict): Additional metadata (JSON).
        created_time (datetime): Timestamp when the source was created.
        updated_time (datetime): Timestamp when the source was last updated.
    """
    __tablename__ = "sources"

    # Primary key - simple string identifier
    # Examples: "mu2e-docdb", "mu2e-wiki", "atlas-docdb"
    id = Column(String(256), primary_key=True)

    # Source metadata
    name = Column(String(512), nullable=True)  # Human-readable name
    description = Column(Text, nullable=True)
    base_uri = Column(String(2048), nullable=True)  # Base URI for this source
    meta = Column(JSONB, nullable=True, default=dict)  # Additional metadata (JSONB for PostgreSQL, JSON for SQLite)

    # Timestamps
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        onupdate=func.now(),
        server_default=func.now(),
    )

    # Relationships
    documents = relationship("Document", back_populates="source_ref")
    raw_documents = relationship("RawDocument", back_populates="source_ref")

    def __repr__(self) -> str:
        return f"<Source(id={self.id}, name={self.name})>"


class Document(Base):
    """Document table 'documents' for storing LLM-ready knowledge base documents.

    This table stores processed documents that are ready to be used by LLMs. The original
    raw files are tracked in the documents_raw table. Documents can be created by parsing
    raw files or generated programmatically.

    Attributes:
        id (str): Primary key (UUID stored as string). Works with both PostgreSQL and SQLite.
        source_id (str): Foreign key to the sources table.
        raw_document_id (str): Foreign key to documents_raw table (optional - not all documents come from raw files).
        parser_id (str): Foreign key to parsers table (optional - tracks which parser was used).
        doc_id (str): Human-readable document identifier within the source (e.g., "page-42"). Not necessarily unique within the source.
        uri (str): URI where the raw document can be accessed (optional).
        source_type (str): MIME type of the source (e.g., "application/pdf", "text/html").
        doc_type (str): Document category (e.g., "text", "image", "mixed").
        text (str): Extracted text content that can be sent to LLMs.
        binary (bytes): Binary data for images/diagrams extracted from documents that can be sent to multimodal LLMs.
                       Not used for storing original document files (those go in documents_raw table via file_path).
        meta (dict): Flexible JSON field for additional metadata (authors, keywords, etc.).
        creating_time (datetime): Timestamp when the document was created.
        update_time (datetime): Timestamp when the document was last updated.
        insert_time (datetime): Timestamp when the document was inserted into the KB.
        parent_id (str): Parent document reference for hierarchical documents (optional).
                        Used for extracted images to reference the text document they came from.
        title (str): Extracted title of the document (optional).
        title_gen (str): LLM-generated title.
        summary (str): LLM-generated detailed summary for retrieval and display.
        gist (str): LLM-generated high-level concepts/themes for embedding context.
        content_hash (str): Hash of the document content for deduplication.
    """

    __tablename__ = "documents"

    # Primary key - UUID stored as string for simplicity (works with both PostgreSQL and SQLite)
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Foreign key to source table
    source_id = Column(
        String(256),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Foreign key to documents_raw table (optional - not all documents come from raw files)
    raw_document_id = Column(
        String(36),
        ForeignKey("documents_raw.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Foreign key to parsers table (optional - tracks which parser framework was used)
    # References parsers.name (e.g., "kb-mcp", "docling", "manual")
    parser_id = Column(
        String(128),
        ForeignKey("parsers.name", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Human-readable document identifier within the source
    # Examples: "1234", "1234-doc1", "page-42"
    # Unique per source (enforced by composite unique constraint)
    doc_id = Column(String(512), nullable=True, index=True)

    # URI - where the raw document can be accessed (optional)
    uri = Column(String(2048), nullable=True, index=True)

    # Source type - MIME type (e.g., "application/pdf", "text/html", "application/json")
    source_type = Column(String(128), nullable=False, index=True)

    # Document type - text vs images vs other
    # Examples: "text", "image", "mixed", "table", etc.
    doc_type = Column(String(64), nullable=False, default="text", index=True)

    # Content - can store both text and binary
    # For text documents: use text for extracted/descriptive text
    # For binary documents (images, PDFs): use binary for the actual data
    # Some documents might have both (e.g., image with LLM-generated description in text)
    text = Column(Text, nullable=True)
    binary = Column(LargeBinary, nullable=True)

    # Meta - flexible JSON field for additional metadata
    meta = Column(JSONB, nullable=True, default=dict)  # JSONB for PostgreSQL, JSON for SQLite

    # Timestamps
    # creating_time: when document was created in source system
    creating_time = Column(DateTime(timezone=True), nullable=True, index=True)
    update_time = Column(DateTime(timezone=True), nullable=True, index=True)
    insert_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )
    # Parent document reference (for hierarchical documents)
    parent_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # title: Extracted from document metadata if present
    title = Column(Text, nullable=True)
    # LLM-generated fields
    # title_gen: LLM-generated title, optional
    title_gen = Column(Text, nullable=True)
    # summary: LLM-generated detailed summary for search/retrieval and display
    summary = Column(Text, nullable=True)
    # gist: LLM-generated high-level concepts/themes for embedding context
    gist = Column(Text, nullable=True)
    # Content hash for deduplication
    # Stored here for convenience - can check duplicates quickly
    # Alternative would be separate deduplication table, but this is simpler for now
    content_hash = Column(String(64), nullable=True, index=True)
    # Relationships
    source_ref = relationship("Source", back_populates="documents")
    raw_document = relationship("RawDocument", back_populates="documents")
    parser = relationship("Parser", back_populates="documents")
    parent = relationship("Document", remote_side=[id], backref="children")

    # Indexes for common queries
    # index=True on columns creates single-column indexes
    # Composite indexes are defined in __table_args__
    __table_args__ = (
        #Index("idx_documents_source_type", "source_id", "source_type"),
        Index("idx_documents_insert_time", "insert_time"),
        Index("idx_documents_content_hash", "content_hash"),
        Index("idx_documents_source_doc_id", "source_id", "doc_id"),
        # Index for deduplication by identity (source_id, doc_id, parser_id)
        Index("idx_documents_source_doc_parser", "source_id", "doc_id", "parser_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Document(id={self.id}, source_id={self.source_id}, "
            f"doc_id={self.doc_id}, uri={self.uri})>"
        )

    def chunk(self, chunk_strategy: Optional[str] = None, config: Optional[dict] = None):
        """Chunk this document and save chunks to the database.

        This is a convenience method that calls chunk_document() from the embedding module.
        Uses the object's session if attached, otherwise creates a new session.

        Args:
            chunk_strategy: Optional chunking strategy ("tokens", "slide", or "summary").
                           If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
                           "summary" creates a single chunk from document.summary field.
            config: Optional chunking configuration

        Returns:
            List of Chunk objects (saved to database)

        Example:
            ```python
            doc = get(uuid="abc-123")
            chunks = doc.chunk(chunk_strategy="tokens", config={"chunk_size": 500})
            ```
        """
        from .embedding import chunk_document

        # Use object's session if attached, otherwise None (will create new session)
        obj_state = sqlalchemy_inspect(self)
        session = obj_state.session if obj_state.session is not None else None

        return chunk_document(self, chunk_strategy=chunk_strategy, config=config, session=session)

    def get_chunks(self, chunk_strategy: Optional[str] = None):
        """Get all chunks for this document.

        This is a convenience method that calls get_chunks() from the embedding module.
        Uses the object's session if attached, otherwise creates a new session.

        Args:
            chunk_strategy: Optional filter for specific chunking strategy

        Returns:
            If object has session: List of Chunk objects (attached to session)
            If no session: List of chunk dictionaries

        Example:
            ```python
            doc = get(uuid="abc-123")
            chunks = doc.get_chunks(chunk_strategy="tokens_1000_200")
            ```
        """
        from .embedding import get_chunks

# Use object's session if attached, otherwise None (will create new session)
        obj_state = sqlalchemy_inspect(self)
        session = obj_state.session if obj_state.session is not None else None

        return get_chunks(document_id=self.id, chunk_strategy=chunk_strategy, session=session)

    def drop_chunks(self, chunk_strategy: Optional[str] = None) -> int:
        """Drop chunks for this document.

        This is a convenience method that calls drop_chunks() from the embedding module.
        Uses the object's session if attached, otherwise creates a new session.

        Args:
            chunk_strategy: Optional filter for specific chunking strategy.
                           If provided, only drops chunks with this strategy.
                           If None, drops ALL chunks for the document.

        Returns:
            Number of chunks deleted

        Example:
            ```python
            doc = get(uuid="abc-123")
            count = doc.drop_chunks(chunk_strategy="tokens_1000_200")
            print(f"Deleted {count} chunks")
            ```
        """
        from .embedding import drop_chunks

# Use object's session if attached, otherwise None (will create new session)
        obj_state = sqlalchemy_inspect(self)
        session = obj_state.session if obj_state.session is not None else None

        return drop_chunks(document_id=self.id, chunk_strategy=chunk_strategy, session=session)

    def chunk_and_embed(
        self,
        chunk_strategy: Optional[str] = None,
        chunk_config: Optional[dict] = None,
        embedding_name: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        batch_size: Optional[int] = None,
        **kwargs,
    ):
        """Chunk this document and embed all chunks.

        This is a convenience method that calls chunk_and_embed() from the embedding module.
        Similar to doc.chunk() but also embeds the chunks after creating them.
        Uses the object's session if attached, otherwise creates a new session.

        Args:
            chunk_strategy: Optional chunking strategy ("tokens", "slide", or "summary").
                           If None, reads from CHUNK_STRATEGY env var, defaults to "tokens".
                           "summary" creates a single chunk from document.summary field.
            chunk_config: Optional chunking configuration
            embedding_name: Optional short name for the embedding config (e.g., "openai-small")
                           If None, uses provider/model or env vars.
            provider: Optional provider name (used if embedding_name is not provided)
            model: Optional model name (used if embedding_name is not provided)
            batch_size: Optional batch size for embedding generation
            **kwargs: Additional parameters passed to embedder

        Returns:
            List of Chunk objects (saved to database and embedded)

        Example:
            ```python
            doc = get(uuid="abc-123")
            chunks = doc.chunk_and_embed(embedding_name="openai-small")
            ```
        """
        from .embedding import chunk_and_embed

# Use object's session if attached, otherwise None
        obj_state = sqlalchemy_inspect(self)
        session = obj_state.session if obj_state.session is not None else None

        return chunk_and_embed(
            self,
            chunk_strategy=chunk_strategy,
            chunk_config=chunk_config,
            embedding_name=embedding_name,
            provider=provider,
            model=model,
            batch_size=batch_size,
            session=session,
            **kwargs
        )

    def generate_summary(
        self,
        include_title: bool = True,
        include_gist: bool = True,
        include_summary: bool = True,
        model: Optional[str] = None,
    ) -> "Document":
        """Generate and save AI summary, gist, and/or title for this document.

        Calls the summary generation function and updates the document fields
        (title_gen, gist, summary) in the database. Also creates a SummaryLog entry.
        Uses the object's session if attached, otherwise creates a new session.

        Args:
            include_title: Whether to generate AI title (default: False)
            include_gist: Whether to generate gist (default: True)
            include_summary: Whether to generate summary (default: True)
            model: Optional model name to use (overrides SUMMARY_MODEL env var)

        Returns:
            Self (Document) for method chaining

        Example:
            ```python
            doc = get(uuid="abc-123")
            doc.generate_summary(include_title=True)
            print(f"Title: {doc.title_gen}")
            ```
        """
        import os
        import socket

        from ..summary import summarize
        from .embedding.db_models import SummaryLog

# Skip summary generation for image documents
        # Image documents already have descriptions in their text field
        if self.doc_type == "image":
            logger.info(f"Skipping summary generation for image document {self.id}")
            return self

        # Get text to summarize
        if not self.text or not self.text.strip():
            raise ValueError(f"Document {self.id} has no text content to summarize")

        # Use object's session if attached, otherwise create new session
        obj_state = sqlalchemy_inspect(self)
        session_orig = obj_state.session
        should_close = session_orig is None

        from .database import get_db_session
        with get_db_session(session_orig) as session:
            from ..config import get_llm_config
            # Determine the actual model that will be used (same logic as summarize function)
            # This ensures the log entry uses the same model name that's actually used
            if not model:
                llm_config = get_llm_config()
                model = llm_config['summary_model']
            actual_model = model

            # Generate summary/gist/title
            result = summarize(
                text=self.text,
                include_title=include_title,
                include_gist=include_gist,
                include_summary=include_summary,
                model=model,
            )

            # If not attached to session, merge this document
            doc = session.merge(self) if should_close else self

            # Update document fields
            if include_title and "title" in result:
                doc.title_gen = result["title"]
            if include_gist and "gist" in result:
                doc.gist = result["gist"]
            if include_summary and "summary" in result:
                doc.summary = result["summary"]

            # Create log entry using the actual model that was used
            log_entry = SummaryLog(
                document_id=doc.id,
                model=actual_model,
                time_summary=result.get("time_summary", 0.0),
                hostname=socket.gethostname(),
                meta={"query": result.get("query", "")},
            )
            session.add(log_entry)
            
            # Flush to ensure SummaryLog is visible for subsequent operations in the same session
            # Context manager handles commit/rollback automatically
            # If should_close=True, it will commit; if False, caller handles commit
            session.flush()
            
            return doc


    @classmethod
    def from_dict(cls, data: dict) -> "Document":
        """Create a Document instance from a dictionary.

        Args:
            data: Dictionary with document fields. Required fields:
                - source_id, source_type
                - Either text or binary

        Returns:
            Document instance (not yet saved to database)

        Example:
            ```python
            # Title can be provided directly or in meta
            doc = Document.from_dict({
                "source_id": "mu2e-docdb",
                "doc_id": "1234-doc1",
                "uri": "https://example.com/doc",
                "source_type": "application/pdf",
                "text": "Document content...",
                "meta": {"title": "Document Title", "author": "John Doe"},
            })
            # Title field will be populated from meta["title"]
            ```
        """
        if "source_id" not in data:
            raise ValueError("source_id is required")

        if "doc_id" not in data:
            raise ValueError("doc_id is required")

        if "text" not in data and "binary" not in data:
            raise ValueError("either text or binary must be provided")

        # if text is present but source_type is not set, set it to "text"
        if "text" in data and "source_type" not in data:
            data["source_type"] = "text/plain"

        # Extract meta dict (default to empty dict) - make a copy to avoid mutating input
        meta_raw = data.get("meta", {})
        if isinstance(meta_raw, dict):
            meta = dict(meta_raw)  # Make a shallow copy
        else:
            meta = {}
        
        # Extract title: check data["title"] first, then meta["title"]
        title = data.get("title")
        if not title and meta:
            title = meta.pop("title", None)  # Remove from meta to avoid duplication

        # Extract fields, using defaults where appropriate
        return cls(
            id=data.get("id"),  # Allow explicit ID, otherwise will be generated
            source_id=data["source_id"],
            doc_id=data.get("doc_id"),
            uri=data.get("uri"),
            source_type=data.get("source_type", "application/octet-stream"),
            doc_type=data.get("doc_type", "text"),
            text=data.get("text"),
            binary=data.get("binary"),
            title=title,  # Populate from data["title"] or meta["title"] (removed from meta)
            meta=meta,
            creating_time=data.get("creating_time"),
            update_time=data.get("update_time"),
            parent_id=data.get("parent_id")
        )

    def to_dict(self) -> dict:
        """Convert Document instance to dictionary.
        
        Returns:
            Dictionary representation of the document (binary is included as bytes)
            
        Example:
            doc = Document.from_dict({...})
            doc_dict = doc.to_dict()
            # Binary data is included as bytes in the dict
        """
        result = {
            "id": self.id,
            "source_id": self.source_id,
            "doc_id": self.doc_id,
            "uri": self.uri,
            "source_type": self.source_type,
            "doc_type": self.doc_type,
            "text": self.text,
            "meta": self.meta if self.meta else {},
        }
        
        # Include binary if present (as bytes - Python dicts can handle this)
        if self.binary:
            result["binary"] = self.binary
        
        # Add timestamps
        if self.creating_time:
            result["creating_time"] = self.creating_time.isoformat()
        if self.update_time:
            result["update_time"] = self.update_time.isoformat()
        if self.insert_time:
            result["insert_time"] = self.insert_time.isoformat()
        
        # Add parent_id if present
        if self.parent_id:
            result["parent_id"] = self.parent_id
        
        return result

    @classmethod
    def from_raw_exists(
        cls,
        raw_document_id: str,
        parser_id: Optional[str] = None,
        session = None,
    ) -> bool:
        """Check if a Document exists for a given RawDocument.

        Args:
            raw_document_id: ID of the RawDocument to check
            parser_id: Optional parser ID to filter by. If None, checks for any parser.
            session: Optional database session. If not provided, creates a new one.

        Returns:
            True if a Document exists, False otherwise

        Example:
            ```python
            from kb_mcp.kb import Document

            # Check if any document exists for this raw document
            exists = Document.from_raw_exists(raw_doc.id)

            # Check if document parsed with specific parser exists
            exists = Document.from_raw_exists(raw_doc.id, parser_id="kb-mcp")
            ```
        """
        from .database import get_db_session

        with get_db_session(session) as db_session:
            query = db_session.query(cls).filter(cls.raw_document_id == raw_document_id)

            if parser_id is not None:
                query = query.filter(cls.parser_id == parser_id)

            return query.first() is not None

    @classmethod
    def from_file(
        cls,
        file_path: str | Path,
        data: Optional[dict] = None,
    ) -> "Document":
        """Create a Document instance from a file path.

        This loads the file's binary data and creates a Document object.
        The Document can then be parsed using parse() for text extraction.

        Args:
            file_path: Path to the document file
            data: Optional dictionary with document fields (same as from_dict).
                  If provided, will be merged with file-derived data.
                  Required fields: source_id, doc_id (or will use defaults)

        Returns:
            Document instance with binary data loaded (not yet saved to database)

        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If source_id is not provided (in data or as default)

        Example:
            from kb_mcp.kb import Document
            from kb_mcp.parser import parse

            # Simple usage
            doc = Document.from_file("document.pdf", {
                "source_id": "mu2e-docdb",
                "doc_id": "1234"
            })
            
            # Parse using dict with binary
            doc_dicts = parse(data={
                "source_id": doc.source_id,
                "doc_id": doc.doc_id,
                "binary": doc.binary,
                "source_type": doc.source_type,
                "meta": doc.meta or {}
            })
            documents = [Document.from_dict(d) for d in doc_dicts]
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Start with provided data or empty dict
        doc_data = dict(data) if data else {}
        
        # Read binary data from file
        binary_data = file_path.read_bytes()
        doc_data["binary"] = binary_data
        
        # Auto-detect MIME type if not provided
        if "source_type" not in doc_data:
            from kb_mcp.parser import detect_mime_type
            mime_type = detect_mime_type(file_path)
            if not mime_type:
                mime_type = "application/octet-stream"
            doc_data["source_type"] = mime_type
        
        # Default doc_id to filename without extension if not provided
        if "doc_id" not in doc_data:
            doc_data["doc_id"] = file_path.stem

        if "source_id" not in doc_data:
            doc_data["source_id"] = "local"
        
        # Default uri to file:// absolute path if not provided
        if "uri" not in doc_data:
            doc_data["uri"] = f"file://{file_path.absolute()}"
        
        # Add file metadata to meta dict
        if "meta" not in doc_data:
            doc_data["meta"] = {}
        
        # Add file properties to metadata
        file_stat = file_path.stat()
        doc_data["meta"].update({
            "filename": file_path.name,
            "filepath": str(file_path.absolute()),
            "filesize": file_stat.st_size,
        })
        
        # Validate required fields
        if "source_id" not in doc_data:
            raise ValueError("source_id is required in data dictionary")
        
        # Use from_dict to create the document (reuses validation logic)
        return cls.from_dict(doc_data)
