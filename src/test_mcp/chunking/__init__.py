"""Text chunking module for preparing documents for embedding generation.

Independent module (like parser) that takes text and returns chunk dictionaries.
"""

from .chunking import chunk, count_tokens

__all__ = [
    "chunk",
    "count_tokens",
]

