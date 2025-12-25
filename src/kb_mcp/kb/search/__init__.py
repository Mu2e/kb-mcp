"""Search functionality for knowledge base using vector embeddings and full-text search."""

from .search import search, search_semantic
from .search_fulltext import search_fulltext
from .search_hybrid import search_hybrid
from .similarity import get_closest, get_similar
from .filters import get_filters_fallback

__all__ = [
    "search",  # Main hybrid search (semantic + fulltext with RRF)
    "search_semantic",  # Semantic-only search using embeddings
    "search_fulltext",  # Full-text-only search using tsvector
    "search_hybrid",  # Explicit hybrid search function
    "get_closest",  # Low-level similarity search with embedding vector
    "get_similar",  # Find similar documents/chunks
    "get_filters_fallback",  # Filter utility
]

