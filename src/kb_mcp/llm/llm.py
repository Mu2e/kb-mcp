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
    - OPENAI_BASE_URL_MODELS: JSON map of model -> base URL (optional)
    - OPENAI_API_KEY_MODELS: JSON map of model -> API key (optional)

    Keys in both maps are exact model names, or a prefix ending in "*"
    ("argo:*") to route a whole gateway's models. An exact name wins.

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

    # Create client with optional base URL. A model routed to its own endpoint
    # via OPENAI_BASE_URL_MODELS usually needs that endpoint's own credential,
    # so OPENAI_API_KEY_MODELS overrides the key alongside the URL — otherwise
    # one provider's token gets sent to another provider's host.
    api_key = _lookup_model(llm_config['openai_api_key_models'], model) or api_key

    client_kwargs = {'api_key': api_key}
    base_url = _lookup_model(llm_config['openai_base_url_models'], model) or llm_config['openai_base_url']
    if base_url:
        client_kwargs['base_url'] = base_url
        logger.debug(f"Using OpenAI base URL: {base_url}")

    return ClientClass(**client_kwargs)


def _lookup_model(mapping: dict, model: str = None):
    """Value for `model` in a per-model map: exact name first, then a "prefix*" key."""
    if not model:
        return None
    if model in mapping:
        return mapping[model]
    for key, value in mapping.items():
        if key.endswith("*") and model.startswith(key[:-1]):
            return value
    return None


def parse_json_reply(content: str):
    """Parse a JSON-mode reply, tolerating a markdown code fence around it.

    Some models behind OpenAI-compatible gateways (gemini-2.5-pro via Argo,
    for one) accept `response_format={"type": "json_object"}` but still wrap
    the object in ```json ... ```. Anything else unparseable still raises
    json.JSONDecodeError.
    """
    import json

    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        if not content.startswith("```"):
            raise
        return json.loads(content.strip("`").removeprefix("json").strip())
