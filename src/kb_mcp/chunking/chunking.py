"""Text chunking utility for preparing documents for embedding generation.

Based on code from mu2eDocChat:
https://github.com/corrodis/mu2eDocChat/blob/main/mu2e/chunking.py

"""

import logging
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


def _chunk_by_tokens(
    text: str,
    encoding: Optional[Any] = None,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    strategy_suffix: str = "",
    **kwargs,  # Accept other params but don't use them
) -> List[Dict[str, Any]]:
    """Chunk text by token count with sliding window.

    Args:
        text: Input text to chunk
        chunk_size: Target chunk size in tokens
        chunk_overlap: Overlap between chunks in tokens
        encoding: Optional tiktoken encoding object. If None, uses default encoding.
        strategy_suffix: Optional suffix to append to strategy name (e.g., "_no_gist")

    Returns:
        List of dictionaries with chunk information (without meta field)
    """
    if encoding is None:
        encoding = _get_encoding()
    
    # Generate chunk strategy string for this specific strategy
    # Format: tokens_chunk_size_chunk_overlap[suffix]
    chunk_strategy = f"tokens_{chunk_size}_{chunk_overlap}{strategy_suffix}"
    
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


def _chunk_by_slide(
    text: str,
    encoding: Optional[Any] = None,
    strategy_suffix: str = "",
    **kwargs,  # Accept other params but don't use them for strategy string
) -> List[Dict[str, Any]]:
    """Chunk text using sliding window strategy.

    TODO: Implement sliding window strategy if needed.
    When implemented, this strategy will have its own parameters (not chunk_size/chunk_overlap).

    Args:
        text: Input text to chunk
        encoding: Optional tiktoken encoding object. If None, uses default encoding.
        strategy_suffix: Optional suffix to append to strategy name (e.g., "_no_gist")
        **kwargs: Other parameters (ignored for now, but may be used when fully implemented)

    Returns:
        List of dictionaries with chunk information (without meta field)
    """
    if encoding is None:
        encoding = _get_encoding()
    
    # Generate chunk strategy string for this specific strategy
    chunk_strategy = f"slide{strategy_suffix}"
    
    # For now, use token-based chunking but with slide strategy string
    logger.info("Slide strategy not yet implemented, using token-based chunking")
    # Temporarily use token-based chunking with default params
    # When slide is fully implemented, replace this with actual slide logic
    chunks = _chunk_by_tokens(text, encoding)
    # Override chunk_strategy in all chunks
    for chunk in chunks:
        chunk["chunk_strategy"] = chunk_strategy
    return chunks


def chunk(
    text: str,
    strategy: str = "tokens",
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Chunk text using specified strategy.

    Args:
        text: Input text to chunk
        strategy: Chunking strategy ("tokens" or "slide")
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

    # set defaults
    if config is None:
        config = {}

    if "model" not in config:
        config["model"] = "cl100k_base"
    
    # Ensure chunk_size and chunk_overlap have defaults if None or missing
    if "chunk_size" not in config or config["chunk_size"] is None:
        config["chunk_size"] = 1000
    if "chunk_overlap" not in config or config["chunk_overlap"] is None:
        config["chunk_overlap"] = 200
    
    # Get prepending flags from config (default to True)
    prepend_gist = config.get("prepend_gist", True)
    prepend_section_path = config.get("prepend_section_path", True)
    
    # Calculate strategy suffix from prepending configuration
    strategy_suffix = get_chunk_strategy_suffix(
        prepend_gist=prepend_gist,
        prepend_section_path=prepend_section_path
    )
    
    encoding = _get_encoding(config["model"])

    # Call strategy-specific function
    if strategy == "tokens":
        chunks = _chunk_by_tokens(text, encoding, strategy_suffix=strategy_suffix, **config)
    elif strategy == "slide":
        chunks = _chunk_by_slide(text, encoding, strategy_suffix=strategy_suffix, **config)
    else:
        logger.warning(f"Unknown strategy '{strategy}', using 'tokens'")
        chunks = _chunk_by_tokens(text, encoding, strategy_suffix=strategy_suffix, **config)
    
    # Add meta to all chunks after getting results

    for chunk in chunks:
        chunk["meta"] = chunk.get("meta",{}) | config.copy()
    
    return chunks
