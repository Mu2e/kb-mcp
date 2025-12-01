"""Knowledge base module for document storage and retrieval."""

from .base import add, add_from_path, add_many, add_source, get
from .core import Document, Source
from .database import get_db_session, init_db
from .utils import deduplicate, find_all_duplicates, get_stats, list_sources

__all__ = [
    "add",
    "add_from_path",
    "add_many",
    "add_source",
    "get",
    "deduplicate",
    "find_all_duplicates",
    "get_stats",
    "list_sources",
    "Document",
    "Source",
    "get_db_session",
    "init_db",
]

