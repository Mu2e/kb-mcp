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
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.inspection import inspect as sqlalchemy_inspect

Base = declarative_base()


class Source(Base):
    """Source system model (e.g., mu2e-docdb, mu2e-wiki)."""

    __tablename__ = "sources"

    # Primary key - simple string identifier
    # Examples: "mu2e-docdb", "mu2e-wiki", "atlas-docdb"
    id = Column(String(256), primary_key=True)

    # Source metadata
    name = Column(String(512), nullable=True)  # Human-readable name
    description = Column(Text, nullable=True)
    base_uri = Column(String(2048), nullable=True)  # Base URI for this source
    meta = Column(JSON, nullable=True, default=dict)  # Additional metadata (JSON)

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

    def __repr__(self) -> str:
        return f"<Source(id={self.id}, name={self.name})>"


class Document(Base):
    """Document model for storing knowledge base documents."""

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
    meta = Column(JSON, nullable=True, default=dict)

    # Timestamps
    # creating_time: when document was created in source system
    creating_time = Column(DateTime(timezone=True), nullable=True, index=True)
    # update_time: when document was last updated in source system
    update_time = Column(DateTime(timezone=True), nullable=True, index=True)
    # insert_time: when document was inserted into our system
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
    parent = relationship("Document", remote_side=[id], backref="children")

    # Indexes for common queries
    # index=True on columns creates single-column indexes
    # Composite indexes are defined in __table_args__
    __table_args__ = (
        #Index("idx_documents_source_type", "source_id", "source_type"),
        Index("idx_documents_insert_time", "insert_time"),
        Index("idx_documents_content_hash", "content_hash"),
        Index("idx_documents_source_doc_id", "source_id", "doc_id"),
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
            >>> doc = get(uuid="abc-123")
            >>> chunks = doc.chunk(chunk_strategy="tokens", config={"chunk_size": 500})
        """
        try:
            from .embedding import chunk_document
        except ImportError:
            raise ImportError(
                "Embedding module not available. Install with: pip install -e '.[embedding]'"
            )

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
            >>> doc = get(uuid="abc-123")
            >>> chunks = doc.get_chunks(chunk_strategy="tokens_1000_200")
        """
        try:
            from .embedding import get_chunks
        except ImportError:
            raise ImportError(
                "Embedding module not available. Install with: pip install -e '.[embedding]'"
            )

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
            >>> doc = get(uuid="abc-123")
            >>> count = doc.drop_chunks(chunk_strategy="tokens_1000_200")
            >>> print(f"Deleted {count} chunks")
        """
        try:
            from .embedding import drop_chunks
        except ImportError:
            raise ImportError(
                "Embedding module not available. Install with: pip install -e '.[embedding]'"
            )

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
            >>> doc = get(uuid="abc-123")
            >>> chunks = doc.chunk_and_embed(embedding_name="openai-small")
        """
        try:
            from .embedding import chunk_and_embed
        except ImportError:
            raise ImportError(
                "Embedding module not available. Install with: pip install -e '.[embedding]'"
            )

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
            >>> doc = get(uuid="abc-123")
            >>> doc.generate_summary(include_title=True)
            >>> print(f"Title: {doc.title_gen}")
        """
        import os
        import socket

        try:
            from ..summary import summarize
            from .embedding.core import SummaryLog
        except ImportError:
            raise ImportError(
                "Summary module not available. Install required dependencies."
            )

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
        session = obj_state.session
        own_session = session is None

        if own_session:
            from .database import get_db_session
            session = get_db_session().__enter__()

        try:
            # Determine the actual model that will be used (same logic as summarize function)
            # This ensures the log entry uses the same model name that's actually used
            actual_model = model if model else os.getenv('SUMMARY_MODEL', 'gemini-2.5-flash-lite')
            
            # Generate summary/gist/title
            result = summarize(
                text=self.text,
                include_title=include_title,
                include_gist=include_gist,
                include_summary=include_summary,
                model=model,
            )

            # If not attached to session, merge this document
            doc = session.merge(self) if own_session else self

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
            session.commit()

            # If we created our own session, refresh and expunge to keep object accessible after session closes
            if own_session:
                session.refresh(doc)
                session.expunge(doc)

            return doc

        finally:
            if own_session:
                session.__exit__(None, None, None)

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
            from test_mcp.kb import Document
            from test_mcp.parser import parse

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
            try:
                from ..parser import detect_mime_type
                mime_type = detect_mime_type(file_path)
            except ImportError:
                # Fallback to mimetypes if parser not available
                import mimetypes
                mime_type, _ = mimetypes.guess_type(str(file_path))
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
