"""Search functionality for knowledge base using vector embeddings."""

from .search import search
from .filters import get_filters_fallback
from .logs import get_search_logs

__all__ = ["search", "get_filters_fallback", "get_search_logs"]

