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


def _completion(text):
    from types import SimpleNamespace
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)


def test_gateway_notice_is_raised_not_returned():
    from kb_mcp.llm import GatewayLimitExceeded, check_gateway_notice

    notice = "\n\n⚠️ **IMPORTANT USAGE NOTICE FROM ARGO** ⚠️\n🚫 **ACCESS REVOKED** 🚫\nDaily user limit exceeded."
    with pytest.raises(GatewayLimitExceeded, match="ACCESS REVOKED"):
        check_gateway_notice(_completion(notice))
    ok = _completion("The stopping target is aluminum.")
    assert check_gateway_notice(ok) is ok
    assert check_gateway_notice(_completion(None)).choices[0].message.content is None


def test_judge_errors_are_unjudged_not_wrong(monkeypatch):
    import kb_mcp.eval_utils.judge as judge

    client = type("C", (), {})()
    client.chat = type("Ch", (), {})()
    client.chat.completions = type("Co", (), {"create": lambda self, **k: _completion("not json")})()
    monkeypatch.setattr(judge, "get_openai_client", lambda model=None: client)
    monkeypatch.setattr(judge, "record_llm_usage", lambda *a, **k: None)
    assert judge.llm_judge_answer("q?", "a", model="m", mode="answer")["is_hit"] is None


@pytest.mark.parametrize("reply, expected", [
    ('{"is_hit": true, "source_relation": "older_source", "acknowledges_conflict": false, "justification": "x"}',
     (False, "older_source", False)),
    ('{"is_hit": true, "source_relation": "conflicting_source", "acknowledges_conflict": false, "justification": "x"}',
     (False, "conflicting_source", False)),
    ('{"is_hit": true, "source_relation": "conflicting_source", "acknowledges_conflict": true, "justification": "x"}',
     (True, "conflicting_source", True)),
    ('{"is_hit": true, "source_relation": "newer_source", "acknowledges_conflict": false, "justification": "x"}',
     (True, "newer_source", False)),
    ('{"is_hit": false, "source_relation": "made_up", "justification": "x"}',
     (False, "none", False)),
])
def test_judge_v2_source_rules(monkeypatch, reply, expected):
    import kb_mcp.eval_utils.judge as judge

    client = type("C", (), {})()
    client.chat = type("Ch", (), {})()
    client.chat.completions = type("Co", (), {"create": lambda self, **k: _completion(reply)})()
    monkeypatch.setattr(judge, "get_openai_client", lambda model=None: client)
    monkeypatch.setattr(judge, "record_llm_usage", lambda *a, **k: None)
    v = judge.llm_judge_answer("q?", "a", expected_answer="b", model="m", mode="answer", reference_source="mu2e-docdb 1")
    assert (v["is_hit"], v["source_relation"], v["acknowledges_conflict"]) == expected


def test_retrieval_mode_has_no_source_fields(monkeypatch):
    import kb_mcp.eval_utils.judge as judge

    client = type("C", (), {})()
    client.chat = type("Ch", (), {})()
    client.chat.completions = type("Co", (), {"create": lambda self, **k: _completion('{"is_hit": true, "justification": "x"}')})()
    monkeypatch.setattr(judge, "get_openai_client", lambda model=None: client)
    monkeypatch.setattr(judge, "record_llm_usage", lambda *a, **k: None)
    v = judge.llm_judge_answer("q?", "context", model="m", mode="retrieval")
    assert v["source_relation"] is None and v["acknowledges_conflict"] is None
