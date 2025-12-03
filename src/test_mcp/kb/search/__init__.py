"""Search functionality for knowledge base using vector embeddings."""

from .search import search
from .filters import get_filters_fallback

__all__ = ["search", "get_filters_fallback"]

