"""Text chunking utility for preparing documents for embedding generation.

Based on code from mu2eDocChat:
https://github.com/corrodis/mu2eDocChat/blob/main/mu2e/chunking.py

"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any

import tiktoken

logger = logging.getLogger(__name__)


def get_chunk_strategy_suffix(
    prepend_gist: bool = True,
    prepend_section_path: bool = True,
) -> str:
    """
    Get the suffix to append to chunk strategy names based on prepending configuration.
    
    Args:
        prepend_gist: Whether to prepend document gist to chunks (default: True)
        prepend_section_path: Whether to prepend section path to chunks (default: True)
    
    Returns:
        Suffix string: "_no_context", "_no_section", "_no_gist", or "" (empty string)
    """
    if (not prepend_section_path) and (not prepend_gist):
        return "_no_context"
    elif not prepend_section_path:
        return "_no_section"
    elif not prepend_gist:
        return "_no_gist"
    else:
        return ""


def _get_encoding(model: Optional[str] = None):
    """Get tiktoken encoding for a model.
    
    Args:
        model: Optional model name for token counting. If None, uses default encoding.
    
    Returns:
        tiktoken encoding object
    """
    if model is None or model == "cl100k_base":
        # Use default encoding
        return tiktoken.get_encoding("cl100k_base")

    try:
        return tiktoken.encoding_for_model(model)
    except Exception:
        # Fallback to a default encoding if model not found
        logger.warning(
            f"Model '{model}' not found, using default encoding 'cl100k_base'"
        )
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """Count the number of tokens in a text string.

    Args:
        text: Text to count tokens for
        model: Optional model name for token counting. If None, uses default encoding.

    Returns:
        Number of tokens in the text

    Raises:
        ImportError: If tiktoken is not installed
    """
    encoding = _get_encoding(model)
    return len(encoding.encode(text))


# ============================================================================
# Strategy-based Architecture
# ============================================================================

class ChunkStrategy(ABC):
    """Abstract base class for chunking strategies.

    Each strategy must implement two static methods:
    - get_strategy_name: Returns the full strategy name that will be used
    - chunk: Performs the actual chunking

    Both methods use the same logic to ensure consistency.
    """

    @staticmethod
    def _get_prepending_config(config: Dict[str, Any]) -> tuple[bool, bool]:
        """Get prepending configuration with defaults.

        Args:
            config: Configuration dictionary

        Returns:
            Tuple of (prepend_gist, prepend_section_path)
        """
        prepend_gist = config.get("prepend_gist", True)
        prepend_section_path = config.get("prepend_section_path", True)
        return prepend_gist, prepend_section_path

    @staticmethod
    def _create_single_chunk(
        text: str,
        chunk_strategy: str,
        encoding: Any,
        meta: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Create a single chunk from text.

        Helper method for strategies that create only one chunk (summary, image).

        Args:
            text: Text content for the chunk
            chunk_strategy: Strategy name to use
            encoding: tiktoken encoding for token counting
            meta: Optional metadata dict (default: empty dict)

        Returns:
            List containing a single chunk dictionary
        """
        token_length = len(encoding.encode(text))
        return [
            {
                "text": text,
                "char_start_index": 0,
                "char_end_index": len(text),
                "token_length": token_length,
                "chunk_index": 0,
                "chunk_strategy": chunk_strategy,
                "meta": meta or {},
            }
        ]

    @staticmethod
    @abstractmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the full strategy name for this configuration.

        Args:
            config: Configuration dictionary with strategy-specific parameters

        Returns:
            Full strategy name string (e.g., "tokens_1000_200_no_gist")
        """
        pass

    @staticmethod
    @abstractmethod
    def chunk(text: str, config: Dict[str, Any], encoding: Any) -> List[Dict[str, Any]]:
        """Chunk the text according to this strategy.

        Args:
            text: Input text to chunk
            config: Configuration dictionary with strategy-specific parameters
            encoding: tiktoken encoding object for token operations

        Returns:
            List of chunk dictionaries
        """
        pass


class TokensStrategy(ChunkStrategy):
    """Token-based chunking strategy with sliding window."""

    @staticmethod
    def _ensure_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure default values are set in config.

        Args:
            config: Configuration dictionary

        Returns:
            New config dictionary with defaults applied
        """
        result = config.copy()
        result.setdefault("chunk_size", 1000)
        result.setdefault("chunk_overlap", 200)
        return result

    @staticmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the strategy name for token-based chunking.

        Format: tokens_{chunk_size}_{chunk_overlap}[suffix]
        Example: "tokens_1000_200" or "tokens_1000_200_no_gist"
        """
        config = TokensStrategy._ensure_defaults(config)
        chunk_size = config["chunk_size"]
        chunk_overlap = config["chunk_overlap"]
        prepend_gist, prepend_section_path = ChunkStrategy._get_prepending_config(config)

        suffix = get_chunk_strategy_suffix(
            prepend_gist=prepend_gist,
            prepend_section_path=prepend_section_path
        )

        return f"tokens_{chunk_size}_{chunk_overlap}{suffix}"

    @staticmethod
    def chunk(text: str, config: Dict[str, Any], encoding: Any) -> List[Dict[str, Any]]:
        """Chunk text by token count with sliding window."""
        config = TokensStrategy._ensure_defaults(config)
        chunk_size = config["chunk_size"]
        chunk_overlap = config["chunk_overlap"]
        chunk_strategy = TokensStrategy.get_strategy_name(config)

        tokens = encoding.encode(text)
        chunks = []

        if len(tokens) <= chunk_size:
            # Single chunk - return entire text
            token_length = len(tokens)
            return [
                {
                    "text": text,
                    "char_start_index": 0,
                    "char_end_index": len(text),
                    "token_length": token_length,
                    "chunk_index": 0,
                    "chunk_strategy": chunk_strategy,
                    "meta": {
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap,
                    },
                }
            ]

        start_idx = 0
        chunk_index = 0

        while start_idx < len(tokens):
            end_idx = min(start_idx + chunk_size, len(tokens))
            chunk_tokens = tokens[start_idx:end_idx]
            chunk_text = encoding.decode(chunk_tokens)

            # Calculate character positions
            if start_idx == 0:
                char_start = 0
            else:
                # Decode tokens up to start_idx to find char position
                prefix_tokens = tokens[:start_idx]
                prefix_text = encoding.decode(prefix_tokens)
                char_start = len(prefix_text)

            char_end = char_start + len(chunk_text)
            token_length = len(chunk_tokens)

            chunks.append(
                {
                    "text": chunk_text,
                    "char_start_index": char_start,
                    "char_end_index": char_end,
                    "token_length": token_length,
                    "chunk_index": chunk_index,
                    "chunk_strategy": chunk_strategy,
                    "meta": {
                        "chunk_size": chunk_size,
                        "chunk_overlap": chunk_overlap,
                    },
                }
            )

            chunk_index += 1

            # Move start position accounting for overlap
            start_idx = end_idx - chunk_overlap
            if end_idx >= len(tokens):
                break

        return chunks


class SlideStrategy(ChunkStrategy):
    """Sliding window chunking strategy.

    TODO: Implement sliding window strategy if needed.
    For now, this uses token-based chunking.
    """

    @staticmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the strategy name for slide chunking.

        Format: slide[suffix]
        Example: "slide" or "slide_no_gist"
        """
        prepend_gist, prepend_section_path = ChunkStrategy._get_prepending_config(config)

        suffix = get_chunk_strategy_suffix(
            prepend_gist=prepend_gist,
            prepend_section_path=prepend_section_path
        )

        return f"slide{suffix}"

    @staticmethod
    def chunk(text: str, config: Dict[str, Any], encoding: Any) -> List[Dict[str, Any]]:
        """Chunk text using sliding window strategy."""
        chunk_strategy = SlideStrategy.get_strategy_name(config)

        # For now, use token-based chunking but with slide strategy string
        logger.info("Slide strategy not yet implemented, using token-based chunking")
        # Temporarily use token-based chunking with default params
        chunks = TokensStrategy.chunk(text, config, encoding)
        # Override chunk_strategy in all chunks
        for chunk in chunks:
            chunk["chunk_strategy"] = chunk_strategy
        return chunks


class SummaryStrategy(ChunkStrategy):
    """Summary-based chunking strategy.

    This strategy creates a single chunk from the document summary.
    """

    @staticmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the strategy name for summary chunking.

        Format: summary
        """
        return "summary"

    @staticmethod
    def chunk(text: str, config: Dict[str, Any], encoding: Any) -> List[Dict[str, Any]]:
        """Create a single chunk from the summary text."""
        chunk_strategy = SummaryStrategy.get_strategy_name(config)
        return ChunkStrategy._create_single_chunk(text, chunk_strategy, encoding)


class ImageStrategy(ChunkStrategy):
    """Image-based chunking strategy.

    This strategy creates a single chunk from image metadata or description.
    """

    @staticmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the strategy name for image chunking.

        Format: image
        """
        return "image"

    @staticmethod
    def chunk(text: str, config: Dict[str, Any], encoding: Any) -> List[Dict[str, Any]]:
        """Create a single chunk from the image text/metadata."""
        chunk_strategy = ImageStrategy.get_strategy_name(config)
        return ChunkStrategy._create_single_chunk(text, chunk_strategy, encoding)


# Strategy registry - store classes, not instances (all methods are static)
_STRATEGIES: Dict[str, type[ChunkStrategy]] = {
    "tokens": TokensStrategy,
    "slide": SlideStrategy,
    "summary": SummaryStrategy,
    "image": ImageStrategy,
}


def get_strategy_name(strategy: str = "tokens", config: Optional[Dict[str, Any]] = None) -> str:
    """Get the effective strategy name that will be used for chunking.

    This function returns the same strategy name that will be stored in the database
    when chunks are created. Use this to predict the strategy name before chunking.

    Args:
        strategy: Chunking strategy name ("tokens", "slide", "summary", "image").
        config: Optional dictionary with strategy-specific parameters:
            - chunk_size: Target chunk size in tokens (default: 1000)
            - chunk_overlap: Overlap between chunks in tokens (default: 200)
            - prepend_gist: Whether to prepend document gist (default: True)
            - prepend_section_path: Whether to prepend section path (default: True)

    Returns:
        Full strategy name string (e.g., "tokens_1000_200", "tokens_1000_200_no_gist", "summary")

    Examples:
        ```python
        # Get default strategy name
        get_strategy_name("tokens")
        # Returns: "tokens_1000_200"

        # Get strategy name with custom config
        get_strategy_name("tokens", {"chunk_size": 500, "prepend_gist": False})
        # Returns: "tokens_500_200_no_gist"

        # Get summary strategy name
        get_strategy_name("summary")
        # Returns: "summary"
        ```
    """
    # Get the strategy class
    strategy_class = _STRATEGIES.get(strategy, _STRATEGIES["tokens"])

    return strategy_class.get_strategy_name(config or {})


def chunk(
    text: str,
    strategy: str = "tokens",
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Chunk text using specified strategy.

    Args:
        text: Input text to chunk
        strategy: Chunking strategy ("tokens", "slide", "summary", "image")
        config: Optional dictionary with strategy-specific parameters:
            - chunk_size: Target chunk size in tokens (default: 1000)
            - chunk_overlap: Overlap between chunks in tokens (default: 200)
            - model: Model name for token counting (default: "cl100k_base")
            - prepend_gist: Whether to prepend document gist (default: True)
            - prepend_section_path: Whether to prepend section path (default: True)
            - Other strategy-specific parameters

    Returns:
        List of dictionaries with keys:
        - text: chunk text content
        - char_start_index: character position where chunk starts
        - char_end_index: character position where chunk ends
        - token_length: number of tokens in chunk
        - chunk_index: position in document (0-based)
        - chunk_strategy: string identifying the chunking strategy and parameters
        - meta: dictionary with chunking configuration parameters

    Examples:
        ```python
        # Simple usage with defaults
        chunks = chunk("Some long text...")
        chunks[0]["chunk_strategy"]
        # Returns: "tokens_1000_200"

        # With custom config and prepending flags
        chunks = chunk(
            "Some long text...",
            strategy="tokens",
            config={
                "chunk_size": 500,
                "chunk_overlap": 100,
                "prepend_gist": False
            }
        )
        chunks[0]["chunk_strategy"]
        # Returns: "tokens_500_100_no_gist"
        ```
    """

    # Set defaults
    config = config or {}
    config.setdefault("model", "cl100k_base")

    encoding = _get_encoding(config["model"])

    # Get the strategy class
    strategy_class = _STRATEGIES.get(strategy, _STRATEGIES["tokens"])
    if strategy not in _STRATEGIES:
        logger.warning(f"Unknown strategy '{strategy}', using 'tokens'")

    # Use the strategy to chunk the text
    chunks = strategy_class.chunk(text, config, encoding)

    # Add meta to all chunks after getting results
    for chunk_item in chunks:
        chunk_item["meta"] = chunk_item.get("meta", {}) | config.copy()

    return chunks
