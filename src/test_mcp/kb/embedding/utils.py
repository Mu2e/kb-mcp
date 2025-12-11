"""Utility functions for embedding module."""

import os
import logging
import threading
from pathlib import Path
from typing import List, Optional, Type, Dict, Any, Union

from dotenv import load_dotenv

from .embedders import OpenAIEmbedder, SentenceTransformersEmbedder

# Load environment variables from .env file
# Find project root (where .env file is located)
# Go up from src/test_mcp/kb/embedding/utils.py to project root
project_root = Path(__file__).parent.parent.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(env_path)

logger = logging.getLogger(__name__)



# Default values (used when env vars not set)
DEFAULT_PROVIDER = "st"
DEFAULT_MODELS = {
    "openai": "text-embedding-3-small",
    "sentence-transformers": "all-MiniLM-L6-v2",
    "sentence_transformers": "all-MiniLM-L6-v2",
    "st": "all-MiniLM-L6-v2",
}


# Embedder class registry - maps provider names to embedder classes
EMBEDDER_CLASSES: Dict[str, Type] = {
    "openai": OpenAIEmbedder,
    "sentence-transformers": SentenceTransformersEmbedder,
    "sentence_transformers": SentenceTransformersEmbedder,  # Alias
    "st": SentenceTransformersEmbedder,  # Short alias
}

# Thread-safe cache for embedder instances - keyed by (provider, model) tuple
# Note: SentenceTransformers models are generally thread-safe for inference,
# but loading them is expensive, so we cache them. OpenAI clients are stateless
# and can be shared across threads.
_EMBEDDER_CACHE: Dict[tuple, Any] = {}
_EMBEDDER_CACHE_LOCK = threading.Lock()


def get_embedder(
    embedding_name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    session=None,
    **kwargs,
):
    """
    Create an embedder instance. Accepts either embedding_name OR (provider, model).
    If neither is provided, reads from environment variables.

    Args:
        embedding_name: Short name for the embedding config (e.g., "openai-small").
                        If provided, loads provider/model from database.
        provider: Embedding provider name ("openai", "sentence-transformers", etc.)
                  If embedding_name is not provided and provider is None, reads from EMBEDDING_PROVIDER env var, defaults to "openai"
        model: Model name.
               If embedding_name is not provided and model is None, reads from EMBEDDING_MODEL env var,
               then falls back to provider-specific default
        session: Optional database session (only used if embedding_name is provided)
        **kwargs: Additional parameters passed to embedder constructor

    Returns:
        Embedder instance (OpenAIEmbedder or SentenceTransformersEmbedder)

    Raises:
        ValueError: If both embedding_name and (provider, model) are specified
        NotImplementedError: If provider is not supported
        ValueError: If required parameters are missing

    Environment Variables:
        EMBEDDING_PROVIDER: Default provider (e.g., "openai", "sentence-transformers")
        EMBEDDING_MODEL: Default model (e.g., "text-embedding-3-small")

    Examples:
        ```python
        # By embedding name
        emb = get_embedder(embedding_name="openai-small")
        # By provider/model
        emb = get_embedder(provider="openai", model="text-embedding-3-small")
        # With defaults (reads from env vars)
        emb = get_embedder()  # Uses EMBEDDING_PROVIDER and EMBEDDING_MODEL
        embeddings = emb(["text1", "text2"])  # Callable
        dimension = emb.embedding_dimension
        ```
    """
    # Validate: cannot have both embedding_name and (provider, model)
    if embedding_name is not None and (provider is not None or model is not None):
        raise ValueError(
            "Cannot specify both embedding_name and (provider, model). "
            "Use either embedding_name OR (provider, model)."
        )
    
    # If embedding_name is provided, load provider/model from database
    if embedding_name is not None:
        from .db_models import EmbeddingConfig
        from ..database import get_db_session
        from ..database import get_db_session

        should_close = session is None

        with get_db_session(session) as session:
            config = session.query(EmbeddingConfig).filter(
                EmbeddingConfig.short_name == embedding_name
            ).first()

            if not config:
                available = [c.short_name for c in session.query(EmbeddingConfig).all()]
                raise ValueError(
                    f"Embedding config '{embedding_name}' not found. "
                    f"Available embeddings: {available if available else '(none)'}. "
                    f"Use get_embedder(provider=..., model=...) to create a new embedding config."
                )

            # Recursively call with provider/model (without embedding_name to avoid loop)
            return get_embedder(
                embedding_name=None,
                provider=config.provider,
                model=config.model,
                session=None,  # Don't pass session to avoid conflicts
                **kwargs
            )

    # Otherwise, use provider/model (with defaults from env vars if not provided)
    # If neither embedding_name nor (provider, model) are provided, read from env vars
    # Get provider from argument, env var, or default
    from ...config import get_embedding_config
    embedding_config = get_embedding_config()
    provider = provider or embedding_config['provider']

    provider_lower = provider.lower()

    if provider_lower not in EMBEDDER_CLASSES:
        available = ", ".join(set(EMBEDDER_CLASSES.keys()))
        raise NotImplementedError(
            f"Embedding provider '{provider}' not supported. "
            f"Available providers: {available}"
        )

    # Get model from argument, env var, or provider-specific default
    model = model or embedding_config['model'] or DEFAULT_MODELS.get(provider_lower)
    if not model:
        raise ValueError(
            f"model is required for provider '{provider}'. "
            f"Set EMBEDDING_MODEL env var or pass model parameter."
        )

    # Check cache first (only if no kwargs, as kwargs might change behavior)
    # Use thread-safe access to cache
    cache_key = (provider_lower, model)
    if not kwargs:
        with _EMBEDDER_CACHE_LOCK:
            if cache_key in _EMBEDDER_CACHE:
                logger.debug(f"Using cached embedder for {provider_lower}/{model}")
                return _EMBEDDER_CACHE[cache_key]

    embedder_class = EMBEDDER_CLASSES[provider_lower]

    try:
        embedder = embedder_class(model_name=model, **kwargs)
        # Cache the embedder instance (only if no kwargs, as kwargs might change behavior)
        # Use thread-safe access to cache
        if not kwargs:
            with _EMBEDDER_CACHE_LOCK:
                # Double-check pattern: another thread might have created it while we were loading
                if cache_key not in _EMBEDDER_CACHE:
                    _EMBEDDER_CACHE[cache_key] = embedder
                    logger.debug(f"Cached embedder instance for {provider_lower}/{model}")
                else:
                    # Another thread created it, use the cached one instead
                    logger.debug(f"Using embedder cached by another thread for {provider_lower}/{model}")
                    embedder = _EMBEDDER_CACHE[cache_key]
        return embedder
    except Exception as e:
        logger.error(f"Error creating embedder '{provider}': {e}")
        raise


def get_embedding_name(
    embedding_name: Optional[str] = None,
    session=None,
    embedder=None,
) -> str:
    """Get embedding name, using default embedder if not provided."""
    if embedding_name is None:
        if embedder is None:
            embedder = get_embedder(session=session)
        embedding_name = embedder._generate_short_name()
    return embedding_name


def embed(
    texts: List[str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> List[List[float]]:
    """
    Generate embeddings for text chunks (convenience function).

    Args:
        texts: List of text chunks to embed
        provider: Embedding provider name ("openai", "sentence-transformers", etc.)
        model: Model name (if None, uses default for provider)
        **kwargs: Additional parameters passed to embedder

    Returns:
        List of embedding vectors (each is a list of floats)

    Examples:
        ```python
        embeddings = embed(
            ["text1", "text2"],
            provider="openai",
            model="text-embedding-3-small"
        )
        ```
    """
    emb = get_embedder(provider=provider, model=model, **kwargs)
    return emb(texts)


def get_embedding_dimension(
    provider: str = "openai",
    model: Optional[str] = None,
    **kwargs,
) -> int:
    """
    Get embedding dimension for a provider/model combination (convenience function).

    Args:
        provider: Embedding provider name ("openai", "sentence-transformers", etc.)
        model: Model name (if None, uses default for provider)
        **kwargs: Additional parameters passed to embedder

    Returns:
        Embedding dimension

    Examples:
        ```python
        dimension = get_embedding_dimension("openai", "text-embedding-3-small")
        # Returns: 1536
        ```
    """
    emb = get_embedder(provider=provider, model=model, **kwargs)
    return emb.embedding_dimension


