"""Knowledge base module for document storage and retrieval."""

from .base import add, add_many, add_source, get
from .core import Document, Source
from .database import get_db_session, init_db

__all__ = [
    "add",
    "add_many",
    "add_source",
    "get",
    "Document",
    "Source",
    "get_db_session",
    "init_db",
]

