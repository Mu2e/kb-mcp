"""Core knowledge base models."""

import uuid
from datetime import datetime
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

    # Content - can store either text or binary
    # For text documents: use text
    # For binary documents (images, PDFs): use binary
    # We keep both because:
    # - Text is easier to search and query
    # - Binary preserves exact format for images, PDFs, etc.
    # - Some documents might have both (e.g., extracted text from PDF + original PDF)
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
        Index("idx_documents_source_type", "source_id", "source_type"),
        Index("idx_documents_insert_time", "insert_time"),
        Index("idx_documents_content_hash", "content_hash"),
        Index("idx_documents_source_doc_id", "source_id", "doc_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Document(id={self.id}, source_id={self.source_id}, "
            f"doc_id={self.doc_id}, uri={self.uri})>"
        )

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
            doc = Document.from_dict({
                "source_id": "mu2e-docdb",
                "doc_id": "1234-doc1",
                "uri": "https://example.com/doc",
                "source_type": "application/pdf",
                "text": "Document content...",
                "meta": {"author": "John Doe"},
            })
        """
        if "source_id" not in data:
            raise ValueError("source_id is required")

        if "doc_id" not in data:
            raise ValueError("doc_id is required")

        if "text" not in data and "binary" not in data:
            raise ValueError("either text or binary must be provided")

        # if text is present but source_type is not set, set it to "text"
        if "text" in data and "source_type" not in data:
            data["source_type"] = "text"

        # Extract fields, using defaults where appropriate
        return cls(
            id=data.get("id"),  # Allow explicit ID, otherwise will be generated
            source_id=data["source_id"],
            doc_id=data.get("doc_id"),
            uri=data.get("uri"),
            source_type=data["source_type"],
            doc_type=data.get("doc_type", "text"),
            text=data.get("text"),
            binary=data.get("binary"),
            meta=data.get("meta", {}),
            creating_time=data.get("creating_time"),
            update_time=data.get("update_time"),
            parent_id=data.get("parent_id")
        )
