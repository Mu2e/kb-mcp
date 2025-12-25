"""Custom SQLAlchemy types for embeddings."""

import logging
from typing import Any, Optional

from sqlalchemy import TypeDecorator, JSON, Text

logger = logging.getLogger(__name__)


class Vector(TypeDecorator):
    """
    Custom type for storing embedding vectors.
    
    Uses PostgreSQL's vector type (pgvector) when available,
    falls back to JSON for SQLite or when pgvector is not installed.
    
    For pgvector, dimension should be specified when creating the column.
    """

    impl = JSON
    cache_ok = True

    def __init__(self, dimension: Optional[int] = None, *args, **kwargs):
        """
        Initialize Vector type.
        
        Args:
            dimension: Fixed dimension for pgvector (None means variable dimension)
        """
        super().__init__(*args, **kwargs)
        self.dimension = dimension

    def load_dialect_impl(self, dialect):
        """Load the appropriate type based on the database dialect."""
        if dialect.name == "postgresql":
            # Use pgvector for PostgreSQL
            from pgvector.sqlalchemy import Vector as PGVector
            # Use dimension if specified, otherwise None (variable dimension)
            return dialect.type_descriptor(PGVector(self.dimension))
        else:
            # SQLite or other databases - use JSON
            return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect):
        """Convert Python list to database format."""
        if value is None:
            return None
        
        if isinstance(value, list):
            # For PostgreSQL with pgvector, return as list (pgvector handles conversion)
            # For JSON, return as list (JSON column stores lists)
            return value
        
        raise ValueError(f"Expected list, got {type(value)}")

    def process_result_value(self, value: Any, dialect):
        """Convert database format to Python list."""
        if value is None:
            return None
        
        # pgvector returns numpy arrays or lists, JSON returns lists
        if hasattr(value, 'tolist'):
            # Handle numpy arrays from pgvector
            return value.tolist()
        
        if isinstance(value, list):
            return value
        
        # Fallback: try to convert to list
        try:
            return list(value)
        except (TypeError, ValueError):
            logger.warning(f"Could not convert embedding value to list: {type(value)}")
            return value


class TSVector(TypeDecorator):
    """
    Custom type for PostgreSQL full-text search vectors (tsvector).

    Uses PostgreSQL's TSVECTOR type when available,
    falls back to Text for SQLite (full-text search won't work, but won't break).
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Load the appropriate type based on the database dialect."""
        if dialect.name == "postgresql":
            # Use TSVECTOR for PostgreSQL
            from sqlalchemy.dialects.postgresql import TSVECTOR
            return dialect.type_descriptor(TSVECTOR)
        else:
            # SQLite or other databases - use Text (placeholder, won't be functional)
            return dialect.type_descriptor(Text())

