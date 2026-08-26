"""Unit tests for the connection checks.

The point of these checks is to turn silent degradation into a loud failure,
so the cases that matter are the ones where a broken endpoint could still be
reported as healthy: a model that answers but can't see images, a host that
answers with a 500, or a check that raises and takes the whole report with it.
Nothing here touches the network.
"""

import kb_mcp.health as health
from kb_mcp.health import CheckResult, check_llm, check_source, check_vision, format_report


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._content, Exception):
            raise self._content
        return _Response(self._content)


class _FakeClient:
    def __init__(self, content):
        self.chat = type("_Chat", (), {})()
        self.chat.completions = _FakeCompletions(content)


def _patch_client(monkeypatch, content):
    client = _FakeClient(content)
    monkeypatch.setattr(health, "_client", lambda model, timeout: client)
    return client


def test_llm_check_passes_on_any_reply(monkeypatch):
    _patch_client(monkeypatch, "OK")
    result = check_llm("m", "https://host/v1", ["default"])
    assert result.ok
    assert "default" in result.detail


def test_llm_check_tolerates_an_empty_reply(monkeypatch):
    # Reasoning models spend the whole token budget on a thinking trace. The
    # endpoint still served the model, which is all this check claims to know.
    _patch_client(monkeypatch, "")
    result = check_llm("m", "https://host/v1", ["default"])
    assert result.ok
    assert "<empty reply>" in result.detail


def test_llm_check_fails_when_the_endpoint_errors(monkeypatch):
    _patch_client(monkeypatch, RuntimeError("connection refused"))
    result = check_llm("m", "https://host/v1", ["summary"])
    assert not result.ok
    assert "connection refused" in result.detail


def test_vision_check_fails_a_text_only_model(monkeypatch):
    # The failure this whole check exists for: a text-only model accepts the
    # image and refuses politely at HTTP 200, which a plain chat probe calls
    # healthy and every image description then comes back as a placeholder.
    _patch_client(monkeypatch, "I can't see any image in our conversation.")
    result = check_vision("m", "https://host/v1")
    assert not result.ok
    assert "text-only" in result.detail


def test_vision_check_fails_on_empty_content(monkeypatch):
    _patch_client(monkeypatch, "")
    result = check_vision("m", "https://host/v1")
    assert not result.ok


def test_vision_check_passes_when_the_color_is_named(monkeypatch):
    client = _patch_client(monkeypatch, "Red.")
    result = check_vision("m", "https://host/v1")
    assert result.ok
    # Thinking must stay off, or Qwen3.x burns the budget before answering.
    sent = client.chat.completions.calls[0]
    assert sent["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_source_check_accepts_a_redirect_to_sso(monkeypatch):
    class _R:
        status_code = 303

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    result = check_source("docdb", "https://host/", ())
    assert result.ok
    assert "303" in result.detail


def test_source_check_reports_missing_credentials(monkeypatch):
    class _R:
        status_code = 200

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    monkeypatch.delenv("SOME_MISSING_CRED", raising=False)
    result = check_source("docdb", "https://host/", ("SOME_MISSING_CRED",))
    assert "SOME_MISSING_CRED" in result.detail


def test_source_check_fails_on_a_server_error(monkeypatch):
    # The host answers, but the application behind it is down — an import
    # would fail, so calling this "reachable" would be a lie.
    class _R:
        status_code = 500

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    result = check_source("wiki", "https://host/", ())
    assert not result.ok


def test_targets_group_by_model_and_endpoint(monkeypatch):
    # Most stages share one model; probing each stage separately would hit the
    # same endpoint five times and report five identical lines.
    monkeypatch.setattr(health, "get_llm_config", lambda: {
        "openai_base_url": "https://a/v1",
        "openai_base_url_models": {"vision-model": "https://b/v1"},
        "default_model": "chat-model",
        "summary_model": "chat-model",
        "graph_relation_extraction_model": "chat-model",
        "privacy_filter_model": "chat-model",
    })
    monkeypatch.setattr(health, "get_parser_config", lambda: {
        "image_description_model": "vision-model",
        "table_llm_summary": False,
        "table_summary_model": "chat-model",
    })
    monkeypatch.setattr(health, "get_eval_config", lambda: {
        "gen_model": "chat-model", "judge_model": "chat-model",
    })
    monkeypatch.setattr(health, "get_agent_config", lambda: {"agent_model": "chat-model"})

    targets = health.llm_targets()
    assert len(targets) == 2
    by_model = {model: (base_url, stages) for model, base_url, stages in targets}
    assert by_model["chat-model"][0] == "https://a/v1"
    assert "summary" in by_model["chat-model"][1]
    # A model routed elsewhere must be probed against its own endpoint.
    assert by_model["vision-model"] == ("https://b/v1", ["image-description"])


def test_report_counts_a_skip_as_neither_pass_nor_failure():
    results = [
        CheckResult("database", True, "up"),
        CheckResult("embeddings", True, "local", skipped=True),
        CheckResult("llm[m]", False, "boom"),
    ]
    report = format_report(results)
    assert "SKIP" in report
    assert "1 of 3 checks failed" in report
