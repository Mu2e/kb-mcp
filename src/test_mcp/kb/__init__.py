"""Knowledge base module for document storage and retrieval."""

from .base import add, add_from_path, add_many, add_source, get, get_count, get_options, get_children, delete_document
from .core import Document, Source
from .database import get_db_session, init_db
from .utils import deduplicate, find_all_duplicates, get_stats, list_sources

# Import embedding models to ensure they're registered with Base.metadata
# This ensures database tables are created when init_db() is called
try:
    from .embedding.core import Chunk, EmbeddingConfig
except ImportError:
    # Embedding module may not be available if dependencies aren't installed
    Chunk = None
    EmbeddingConfig = None

__all__ = [
    "add",
    "add_from_path",
    "add_many",
    "add_source",
    "get",
    "get_count",
    "get_options",
    "get_children",
    "delete_document",
    "deduplicate",
    "find_all_duplicates",
    "get_stats",
    "list_sources",
    "Document",
    "Source",
    "get_db_session",
    "init_db",
]

# Conditionally add embedding models to exports if available
if Chunk is not None:
    __all__.extend(["Chunk", "EmbeddingConfig"])

