"""LLM client utilities for OpenAI-compatible APIs."""

import logging

from ..config import get_llm_config

logger = logging.getLogger(__name__)


def get_openai_client(model: str = None, use_async: bool = False):
    """Get OpenAI client with configuration from environment variables.

    Args:
        model: Model name to use (optional)
            Used if specific models need different base URLs.
        use_async: If True, return AsyncOpenAI client for async operations.
            If False (default), return synchronous OpenAI client.

    Environment variables:
    - OPENAI_API_KEY: API key (required)
    - OPENAI_BASE_URL: Base URL for OpenAI API (optional)

    Returns:
        OpenAI or AsyncOpenAI client instance

    Raises:
        ValueError: If OPENAI_API_KEY is not set
    """
    if use_async:
        from openai import AsyncOpenAI as ClientClass
    else:
        from openai import OpenAI as ClientClass

    llm_config = get_llm_config()
    api_key = llm_config['openai_api_key']
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable not set. "
            "Set it with: export OPENAI_API_KEY=sk-..."
        )

    # Create client with optional base URL
    client_kwargs = {'api_key': api_key}
    base_url = llm_config['openai_base_url']
    if model:
        if model in llm_config['openai_base_url_models']:
            base_url = llm_config['openai_base_url_models'][model]
    if base_url:
        client_kwargs['base_url'] = base_url
        logger.debug(f"Using OpenAI base URL: {base_url}")

    return ClientClass(**client_kwargs)
