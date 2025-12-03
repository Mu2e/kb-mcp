"""Embedding module for generating embeddings and managing embedding models."""

from .core import Chunk, ChunkStrategy, EmbeddingConfig
from .chunking import chunk_document, get_chunk_strategies, get_chunks, drop_chunks
from .embedding import embed_chunk, embed_chunks, chunk_and_embed, get_embedding_names, drop_embedding_table, drop_embedding, get_embeddings, get_embedding_vector
from .embedders import OpenAIEmbedder, SentenceTransformersEmbedder
from .utils import (
    get_embedder,
    embed,
    get_embedding_dimension,
)

__all__ = [
    "Chunk",
    "ChunkStrategy",
    "EmbeddingConfig",
    "chunk_document",
    "get_chunk_strategies",
    "get_chunks",
    "drop_chunks",
    "embed_chunk",
    "embed_chunks",
    "chunk_and_embed",
    "get_embedding_names",
    "drop_embedding_table",
    "drop_embedding",
    "get_embeddings",
    "get_embedding_vector",
    "OpenAIEmbedder",
    "SentenceTransformersEmbedder",
    "get_embedder",
    "embed",
    "get_embedding_dimension",
]
