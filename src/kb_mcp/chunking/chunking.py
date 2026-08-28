"""Text chunking utility for preparing documents for embedding generation.

Based on code from mu2eDocChat:
https://github.com/corrodis/mu2eDocChat/blob/main/mu2e/chunking.py

"""

import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any

import tiktoken

logger = logging.getLogger(__name__)

# Token-chunker defaults. Overridable per call via `chunk_config`, and
# process-wide via CHUNK_SIZE / CHUNK_OVERLAP (see `config.get_embedding_config`).
DEFAULT_CHUNK_SIZE = 1000

# Overlap exists so a passage straddling a boundary survives intact in at
# least one chunk. As a fraction it keeps that property at any chunk size —
# ~100 tokens at 1000, ~25 at a 256-token window — and it bounds index
# inflation at 1/(1-f), so 10% costs ~1.11x rather than the 1.25x that 20%
# was costing.
DEFAULT_CHUNK_OVERLAP_FRACTION = 0.1


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

        The overlap defaults to a *fraction* of the chunk size rather than a
        fixed token count. The chunker strides `chunk_size - chunk_overlap`,
        so an absolute default silently degrades as the size shrinks: pairing
        a leftover 200 with a window-sized 254 gives a stride of 54 and
        duplicates the corpus ~4.7x, and an overlap at or above the size
        never advances at all. Hence the clamp as well as the fraction.

        Args:
            config: Configuration dictionary

        Returns:
            New config dictionary with defaults applied
        """
        result = config.copy()
        result.setdefault("chunk_size", DEFAULT_CHUNK_SIZE)
        chunk_size = max(1, int(result["chunk_size"]))
        result["chunk_size"] = chunk_size

        overlap = result.get("chunk_overlap")
        if overlap is None:
            overlap = round(chunk_size * DEFAULT_CHUNK_OVERLAP_FRACTION)
        overlap = max(0, int(overlap))

        # Half the chunk size is the hard ceiling: above it a token appears in
        # three or more chunks, and at `overlap >= chunk_size` the stride is
        # zero and the loop never terminates. Clamping to `chunk_size - 1`
        # would avoid the hang but still allow a stride of 1 — a config that
        # quietly multiplies the index by the chunk size.
        max_overlap = chunk_size // 2
        if overlap > max_overlap:
            logger.warning(
                "chunk_overlap=%d is more than half of chunk_size=%d; "
                "clamping to %d (stride must stay meaningful)",
                overlap, chunk_size, max_overlap,
            )
            overlap = max_overlap

        # Resolved to a concrete integer here, not left as a fraction, so the
        # strategy name stays a literal ("tokens_1000_100").
        result["chunk_overlap"] = overlap
        return result

    @staticmethod
    def get_strategy_name(config: Dict[str, Any]) -> str:
        """Get the strategy name for token-based chunking.

        Format: tokens_{chunk_size}_{chunk_overlap}[suffix]
        Example: "tokens_1000_100" (the defaults) or "tokens_1000_100_no_gist"

        Both numbers are the *resolved* values, so the name reflects the 10%
        default overlap and any clamping rather than what the caller passed.
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

    Not implemented. This previously fell back to token-based chunking while
    still tagging the resulting chunks "slide", which put rows in the database
    that claim a strategy they were not produced by - silently corrupting any
    comparison between chunking strategies. It now raises instead; callers who
    want token chunks should ask for "tokens".
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
        """Not implemented - see the class docstring."""
        raise NotImplementedError(
            "The 'slide' chunking strategy is not implemented. "
            "Use 'tokens' for token-based chunking with overlap."
        )


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


# Strategy families whose stored name carries the embedding window it was
# chunked for — `summary_256`, `summary_512`. Chunks in these families are
# sized to the encoder's window, so the same name under two different windows
# would describe two incompatible chunkings of the same document. The window
# goes in the name so both can co-exist and be told apart.
#
# The window, not the content cap (254): it is stable, and it states which
# encoders can read the chunk without truncating it.
_WINDOW_SUFFIXED = re.compile(r"^(summary)_\d+$")


def base_strategy(name: str) -> str:
    """Strip a window suffix: 'summary_256' -> 'summary'.

    Read-side consumers that ask *what kind* of chunk this is should compare
    `base_strategy(chunk.chunk_strategy)` rather than the raw name, so they
    keep matching both legacy rows (plain `summary`) and window-tagged ones.

    Deliberately anchored on an explicit family list rather than "strip any
    trailing _<digits>": that generic rule would turn `tokens_1000_200` into
    `tokens_1000`.
    """
    if not name:
        return name
    m = _WINDOW_SUFFIXED.match(name)
    return m.group(1) if m else name


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
