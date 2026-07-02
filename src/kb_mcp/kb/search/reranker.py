"""Reranker module for improving search precision after initial retrieval.

Provides cross-encoder reranking to rescore search results using a model
that sees both the query and passage together (unlike bi-encoder embeddings
which encode them independently). This typically yields significant
precision improvements, especially for top-k results.

Usage:
    from kb_mcp.kb.search.reranker import get_reranker

    reranker = get_reranker()  # Returns configured reranker or None if disabled
    if reranker:
        reranked = reranker.rerank(query, search_results)
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class BaseReranker(ABC):
    """Abstract base class for rerankers.

    A reranker takes a query and a list of search results (documents with chunks),
    rescores them using a more expensive model, and returns results reordered by
    the new scores.
    """

    @abstractmethod
    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Rerank search results.

        Args:
            query: The search query.
            results: List of document result dicts (same format as search output).
                Each dict has "chunks" (list of chunk dicts with "text" field),
                plus document metadata ("doc_id", "doc_title", etc.).
            top_k: If set, only return the top-k results after reranking.
                If None, return all results reranked.

        Returns:
            Reranked list of document result dicts. Each chunk gets a
            "rerank_score" field added. Documents are sorted by their
            best chunk's rerank score.
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name used for reranking."""
        ...


class CrossEncoderReranker(BaseReranker):
    """Cross-encoder reranker using sentence-transformers.

    Uses a cross-encoder model that jointly encodes query-passage pairs,
    producing more accurate relevance scores than bi-encoder similarity.

    Default model: cross-encoder/ms-marco-MiniLM-L-6-v2
    - Fast (~50ms for 50 passages on CPU)
    - Good quality for general-purpose reranking
    - 6 layers, 22M parameters

    Alternative models (set via config):
    - cross-encoder/ms-marco-MiniLM-L-12-v2 (higher quality, slower)
    - BAAI/bge-reranker-v2-m3 (multilingual, high quality)
    - cross-encoder/ms-marco-TinyBERT-L-2-v2 (fastest, lower quality)
    """

    _instance = None  # Singleton to avoid reloading model

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self._model_name = model_name
        self._model = None

    @classmethod
    def get_instance(cls, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> "CrossEncoderReranker":
        """Get or create a singleton reranker instance.

        Avoids reloading the model on every search call.
        If model_name changes, creates a new instance.
        """
        if cls._instance is None or cls._instance._model_name != model_name:
            cls._instance = cls(model_name)
        return cls._instance

    def _load_model(self):
        """Lazy-load the cross-encoder model."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            logger.info(f"Loading cross-encoder model: {self._model_name}")
            load_start = time.time()
            self._model = CrossEncoder(self._model_name)
            load_time = time.time() - load_start
            logger.info(f"Cross-encoder loaded in {load_time:.2f}s")
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    def rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Rerank results using cross-encoder scoring.

        For each chunk in the results, the cross-encoder scores the
        (query, chunk_text) pair. Documents are then reordered by their
        best chunk's rerank score.

        Args:
            query: Search query.
            results: Search result dicts with "chunks" containing "text".
            top_k: Max results to return after reranking.

        Returns:
            Reranked results with "rerank_score" added to each chunk.
        """
        if not results:
            return results

        model = self._load_model()

        # Collect all (query, chunk_text) pairs with references back to chunks
        pairs = []
        chunk_refs = []  # (doc_index, chunk_index)

        for doc_idx, doc in enumerate(results):
            for chunk_idx, chunk in enumerate(doc.get("chunks", [])):
                text = chunk.get("text", "")
                if text:
                    pairs.append((query, text))
                    chunk_refs.append((doc_idx, chunk_idx))

        if not pairs:
            return results

        # Score all pairs in a single batch
        rerank_start = time.time()
        scores = model.predict(pairs)
        rerank_time = time.time() - rerank_start

        logger.debug(
            f"Reranked {len(pairs)} chunks in {rerank_time:.3f}s "
            f"({rerank_time / len(pairs) * 1000:.1f}ms/chunk)"
        )

        # Assign scores back to chunks
        for (doc_idx, chunk_idx), score in zip(chunk_refs, scores):
            results[doc_idx]["chunks"][chunk_idx]["rerank_score"] = float(score)

        # Re-sort chunks within each document by rerank score
        for doc in results:
            chunks = doc.get("chunks", [])
            if chunks and "rerank_score" in chunks[0]:
                chunks.sort(key=lambda c: c.get("rerank_score", -999), reverse=True)
                doc["best_rerank"] = chunks[0]["rerank_score"]
            else:
                doc["best_rerank"] = -999.0

        # Re-sort documents by best rerank score
        results.sort(key=lambda d: d.get("best_rerank", -999), reverse=True)

        # Apply top_k limit
        if top_k is not None:
            results = results[:top_k]

        logger.info(
            f"Reranking complete: {len(pairs)} chunks scored in {rerank_time:.3f}s"
        )

        return results


def get_reranker(
    model_name: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[BaseReranker]:
    """Get the configured reranker, or None if disabled.

    Reads configuration from environment/config if args not provided.

    Args:
        model_name: Override reranker model name.
        enabled: Override whether reranking is enabled.

    Returns:
        A BaseReranker instance, or None if reranking is disabled.
    """
    from ...config import get_reranker_config

    config = get_reranker_config()

    if enabled is not None:
        is_enabled = enabled
    else:
        is_enabled = config["enabled"]

    if not is_enabled:
        return None

    if model_name is None:
        model_name = config["model_name"]

    return CrossEncoderReranker.get_instance(model_name)
