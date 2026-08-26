"""Embedding generation classes for different providers."""

import logging
import os
import re
from typing import List, Optional

from .embedder_base import BaseEmbedder

logger = logging.getLogger(__name__)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedding generator."""

    def __init__(
        self,
        model_name: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize OpenAI embedder.

        Args:
            model_name: OpenAI embedding model name (e.g., "text-embedding-3-small")
            api_key: OpenAI API key (if None, reads from OPENAI_API_KEY env var)
            base_url: Custom base URL for OpenAI-compatible API (optional)
            **kwargs: Additional parameters passed to API
        """
        from openai import OpenAI
        from ...config import get_llm_config

        llm_config = get_llm_config()

        self.provider = "openai"
        self.model = model_name
        self.api_key = api_key or llm_config['openai_api_key']
        if not self.api_key:
            raise ValueError(
                "OpenAI API key is required. Set OPENAI_API_KEY env var or pass api_key parameter"
            )

        base_url = base_url or llm_config['openai_base_url']
        self.client = OpenAI(api_key=self.api_key, base_url=base_url)
        self.config = kwargs

        # Cache dimension and max tokens for known models
        self._dimension_cache = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        self._max_tokens_cache = {
            "text-embedding-3-small": 8191,
            "text-embedding-3-large": 8191,
            "text-embedding-ada-002": 8191,
        }

    def generate_embeddings(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
    ) -> List[List[float]]:
        """
        Generate embeddings for text chunks.

        Args:
            texts: List of text chunks to embed
            batch_size: Optional batch size (if None, processes all at once)

        Returns:
            List of embedding vectors (each is a list of floats)
        """
        try:
            if batch_size is None:
                # OpenAI API can handle batches, so process all at once
                response = self.client.embeddings.create(
                    model=self.model,
                    input=texts,
                    **self.config,
                )
                return [item.embedding for item in response.data]
            else:
                # Process in batches
                embeddings = []
                for i in range(0, len(texts), batch_size):
                    batch = texts[i : i + batch_size]
                    response = self.client.embeddings.create(
                        model=self.model,
                        input=batch,
                        **self.config,
                    )
                    batch_embeddings = [item.embedding for item in response.data]
                    embeddings.extend(batch_embeddings)
                return embeddings
        except Exception as e:
            logger.error(f"Error generating OpenAI embeddings: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """Get embedding dimension."""
        # Check cache first
        if self.model in self._dimension_cache:
            return self._dimension_cache[self.model]

        # For unknown models, try to get dimension from API
        try:
            # Make a test call to get dimension (expensive, but only once)
            test_response = self.client.embeddings.create(
                model=self.model, input="test", **self.config
            )
            dimension = len(test_response.data[0].embedding)
            self._dimension_cache[self.model] = dimension
            return dimension
        except Exception as e:
            logger.warning(
                f"Could not determine dimension for {self.model}, defaulting to 1536: {e}"
            )
            return 1536  # Default fallback

    @property
    def max_tokens(self) -> int:
        """Get maximum input tokens for the model."""
        if self.model in self._max_tokens_cache:
            return self._max_tokens_cache[self.model]
        # Default for OpenAI embedding models
        return 8191

    def _generate_short_name(self) -> str:
        """Generate default short name from provider and model."""
        return f"{self.provider}_{self.model.split('-')[-1]}"


class SentenceTransformersEmbedder(BaseEmbedder):
    """SentenceTransformers embedding generator."""

    # Query-side instructions for asymmetric retrieval models, keyed by the
    # model name as it appears in EMBEDDING_MODEL. Each string is fixed by
    # how the model was trained — these are not tunable prompts, and a
    # model absent from this map is symmetric and gets no prefix.
    #
    # BAAI publishes these with bge-*-v1.5; the trailing space is part of
    # the string. Applied to queries only (see `BaseEmbedder.query_prefix`).
    _QUERY_PREFIXES = {
        "bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
        "bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
        "bge-large-en-v1.5": "Represent this sentence for searching relevant passages: ",
    }

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize SentenceTransformers embedder.

        Args:
            model_name: SentenceTransformers model name (e.g., "all-MiniLM-L6-v2")
            device: Device to use ("cpu", "cuda", etc.). If None, auto-detects.
            **kwargs: Additional model parameters
        """
        from sentence_transformers import SentenceTransformer

        self.provider = "sentence-transformers"
        self.model = model_name

        if device is None:
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        self.device = device
        logger.info(f"Loading SentenceTransformers model '{self.model}' on {device}")
        self._model = SentenceTransformer(model_name, device=device, **kwargs)

    def generate_embeddings(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> List[List[float]]:
        """
        Generate embeddings for text chunks.

        Args:
            texts: List of text chunks to embed
            batch_size: Optional batch size (default: 32)
            **kwargs: Additional encoding parameters

        Returns:
            List of embedding vectors (each is a list of floats)
        """
        if batch_size is None:
            batch_size = 32  # Default batch size for SentenceTransformers

        try:
            embeddings = self._model.encode(
                texts, batch_size=batch_size, convert_to_numpy=True, **kwargs
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Error generating SentenceTransformers embeddings: {e}")
            raise

    @property
    def embedding_dimension(self) -> int:
        """Get embedding dimension."""
        return self._model.get_sentence_embedding_dimension()

    @property
    def max_tokens(self) -> int:
        """Get maximum input tokens for the model."""
        # SentenceTransformers models have varying max sequence lengths
        # Most common models use 256 or 512 tokens
        return self._model.max_seq_length

    @property
    def query_prefix(self) -> str:
        """Query-side instruction for asymmetric models (see base class).

        Matched on the bare model name so an org-qualified id
        ("BAAI/bge-small-en-v1.5") resolves the same as a plain one.
        """
        return self._QUERY_PREFIXES.get(self.model.split("/")[-1], "")

    def _generate_short_name(self) -> str:
        """Generate default short name from provider and model.

        This becomes the `embedding_configs` primary key and the suffix of
        the per-model embeddings table, so it has to survive being used as
        an identifier: drop the org qualifier ("BAAI/bge-small-en-v1.5")
        and anything that isn't alphanumeric or an underscore.
        """
        # For sentence-transformers, use "st" prefix
        model_short = self.model.split("/")[-1]
        model_short = model_short.replace("all-", "").replace("-", "")
        model_short = re.sub(r"[^0-9A-Za-z_]", "_", model_short)
        return f"st_{model_short}"
