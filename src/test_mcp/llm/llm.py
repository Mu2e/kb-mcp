"""LLM client utilities for OpenAI-compatible APIs."""

import os
import logging

logger = logging.getLogger(__name__)


def get_openai_client():
    """Get OpenAI client with configuration from environment variables.

    Environment variables:
    - OPENAI_API_KEY: API key (required)
    - OPENAI_BASE_URL: Base URL for OpenAI API (optional)

    Returns:
        OpenAI client instance

    Raises:
        ImportError: If openai package is not installed
        ValueError: If OPENAI_API_KEY is not set
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "openai package not installed. Install with: pip install openai"
        )

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable not set. "
            "Set it with: export OPENAI_API_KEY=sk-..."
        )

    # Create client with optional base URL
    client_kwargs = {'api_key': api_key}
    base_url = os.getenv('OPENAI_BASE_URL')
    if base_url:
        client_kwargs['base_url'] = base_url
        logger.debug(f"Using OpenAI base URL: {base_url}")

    return OpenAI(**client_kwargs)
