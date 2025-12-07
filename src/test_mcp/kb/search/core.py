"""Database models for search functionality."""

import uuid
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)

from ..core import Base


class SearchLog(Base):
    """Search log for tracking search queries and results."""

    __tablename__ = "logs_search"
    
    # Primary key - UUID stored as string
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    
    # Search query
    query = Column(Text, nullable=False, index=True)
    
    # Search parameters
    embedding_name = Column(String(128), nullable=True, index=True)
    max_results = Column(Integer, nullable=False, default=10)
    source_id = Column(String(128), nullable=True, index=True)
    doc_type = Column(String(128), nullable=True, index=True)
    chunking_strategy = Column(String(128), nullable=True, index=True)
    
    # Filter parameters (stored as JSON)
    filter_params = Column(JSON, nullable=True)  # Elasticsearch-style filter
    metadata_filters = Column(JSON, nullable=True)  # Simple key=value filters
    
    # Results - stored as JSON list of dicts with document_id and chunk_ids
    # Format: [{"document_id": "...", "chunk_ids": ["...", "..."]}, ...]
    results = Column(JSON, nullable=False)  # List of result objects with document_id and chunk_ids
    best_similarity = Column(Float, nullable=True)  # Best similarity score across all results
    total_results = Column(Integer, nullable=False, default=0)
    
    # Timing information
    time_search_total = Column(Float, nullable=True)  # Total search time in seconds
    time_embedding = Column(Float, nullable=True)  # Embedding generation time
    time_deduplication = Column(Float, nullable=True)  # Deduplication time
    time_db_fetch = Column(Float, nullable=True)  # Database fetch time (SQLite only)
    time_distance_calc = Column(Float, nullable=True)  # Distance calculation time (SQLite only)
    time_sort = Column(Float, nullable=True)  # Sorting time (SQLite only)
    
    # Timestamp
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )
    
    def __repr__(self) -> str:
        return f"<SearchLog(id={self.id}, query='{self.query[:50]}...', results={self.total_results})>"
    
    def to_dict(self) -> dict:
        """Convert SearchLog instance to dictionary."""
        return {
            "id": self.id,
            "query": self.query,
            "embedding_name": self.embedding_name,
            "max_results": self.max_results,
            "source_id": self.source_id,
            "doc_type": self.doc_type,
            "chunking_strategy": self.chunking_strategy,
            "filter_params": self.filter_params,
            "metadata_filters": self.metadata_filters,
            "results": self.results,
            "best_similarity": self.best_similarity,
            "total_results": self.total_results,
            "time_search_total": self.time_search_total,
            "time_embedding": self.time_embedding,
            "time_deduplication": self.time_deduplication,
            "time_db_fetch": self.time_db_fetch,
            "time_distance_calc": self.time_distance_calc,
            "time_sort": self.time_sort,
            "created_time": self.created_time.isoformat() if self.created_time else None,
        }

