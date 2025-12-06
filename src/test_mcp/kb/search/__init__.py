"""Search functionality for knowledge base using vector embeddings."""

from .search import search
from .similarity import get_closest, get_similar
from .filters import get_filters_fallback

__all__ = ["search", "get_closest", "get_similar", "get_filters_fallback"]

