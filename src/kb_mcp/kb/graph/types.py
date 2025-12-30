"""Custom SQLAlchemy types for graph module."""

import json
import logging
from typing import Any, Optional

from sqlalchemy import TypeDecorator, JSON, String
from sqlalchemy.dialects import postgresql

logger = logging.getLogger(__name__)


class ArrayOfStrings(TypeDecorator):
    """
    Custom type for storing arrays of strings.

    Uses PostgreSQL's ARRAY(String) type when available,
    falls back to JSON for SQLite or when PostgreSQL array is not supported.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        """Load the appropriate type based on the database dialect."""
        if dialect.name == 'postgresql':
            # Use native array for PostgreSQL
            return dialect.type_descriptor(postgresql.ARRAY(String))
        else:
            # SQLite or other databases - use JSON
            return dialect.type_descriptor(JSON)

    def process_bind_param(self, value: Any, dialect):
        """Convert Python list to database format."""
        if value is None:
            return None

        if not isinstance(value, list):
            raise ValueError(f"Expected list, got {type(value)}")

        if dialect.name == 'postgresql':
            # PostgreSQL handles arrays natively
            return value
        else:
            # SQLite stores as JSON string
            return json.dumps(value)

    def process_result_value(self, value: Any, dialect):
        """Convert database format to Python list."""
        if value is None:
            return None

        if dialect.name == 'postgresql':
            # PostgreSQL returns list directly
            return value if isinstance(value, list) else list(value)
        else:
            # SQLite returns JSON string, need to parse
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse aliases JSON: {value}")
                    return []
            elif isinstance(value, list):
                return value
            else:
                logger.warning(f"Unexpected aliases type: {type(value)}")
                return []
