"""Text chunking module for preparing documents for embedding generation.

Independent module (like parser) that takes text and returns chunk dictionaries.
"""

from .chunking import base_strategy, chunk, count_tokens

__all__ = [
    "base_strategy",
    "chunk",
    "count_tokens",
]

