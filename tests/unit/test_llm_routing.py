"""Unit tests for per-model endpoint routing and JSON-mode reply parsing.

Routing decides which host (and which credential) a model's requests go to,
so a wrong match sends one provider's token to another provider.
"""

import json

import pytest

from kb_mcp.llm.llm import _lookup_model, parse_json_reply

ROUTES = {
    "gpt-oss:120b": "https://vllm.example/v1",
    "argo:*": "http://127.0.0.1:64259/v1",
    "argo:special": "https://elsewhere.example/v1",
}


def test_exact_name_routes():
    assert _lookup_model(ROUTES, "gpt-oss:120b") == "https://vllm.example/v1"


def test_prefix_routes_a_whole_gateway():
    assert _lookup_model(ROUTES, "argo:claude-opus-5") == "http://127.0.0.1:64259/v1"


def test_exact_name_beats_prefix():
    assert _lookup_model(ROUTES, "argo:special") == "https://elsewhere.example/v1"


def test_unrouted_model_falls_through():
    # No match means "use the default endpoint", not a partial match.
    assert _lookup_model(ROUTES, "gpt-oss") is None
    assert _lookup_model(ROUTES, "argo") is None
    assert _lookup_model(ROUTES, None) is None


def test_plain_json_reply():
    assert parse_json_reply(' {"is_hit": true} ') == {"is_hit": True}


def test_fenced_json_reply():
    # gemini-2.5-pro via Argo wraps JSON-mode output in a markdown fence.
    assert parse_json_reply('```json\n{"is_hit": true}\n```') == {"is_hit": True}
    assert parse_json_reply('```\n{"is_hit": false}\n```') == {"is_hit": False}


def test_unparseable_reply_still_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json_reply("The answer is yes.")
