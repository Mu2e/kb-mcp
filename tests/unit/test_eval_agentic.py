"""Unit tests for the agentic eval loop's own logic.

The loop is driven by a fake MCP server and a fake model client, so these
cover what the eval client decides -- truncation, turn limit, source tracking,
context overflow -- not the tools or the model.
"""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

import kb_mcp.kb.eval.runner as runner

SOURCE = "11111111-2222-4333-8444-555555555555"


class _FakeServer:
    instructions = "server instructions"

    def __init__(self, results):
        self.results = results  # tool name -> text returned

    async def list_tools(self):
        return [SimpleNamespace(name="kb_search", description="search", input_schema={"type": "object"})]

    async def get_prompt(self, name, args):
        text = SimpleNamespace(text=f"Research: {args['question']}")
        return SimpleNamespace(messages=[SimpleNamespace(role="user", content=text)])

    async def call_tool(self, name, args):
        return SimpleNamespace(content=[SimpleNamespace(text=self.results[name])], is_error=False)


def _tool_call_reply(name="kb_search", args=None):
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(args or {"query": "q"})))
    return SimpleNamespace(content=None, tool_calls=[call], model_dump=lambda **kw: {"role": "assistant"})


def _text_reply(text):
    return SimpleNamespace(content=text, tool_calls=None, model_dump=lambda **kw: {"role": "assistant"})


class _FakeClient:
    """Replays scripted replies; an Exception in the script is raised instead."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=item)], usage=None)


@pytest.fixture
def run_agent(monkeypatch):
    monkeypatch.setattr(runner, "record_llm_usage", lambda *a, **k: None, raising=False)
    monkeypatch.setattr("kb_mcp.llm.record_llm_usage", lambda *a, **k: None)

    def _run(script, results, **kwargs):
        client = _FakeClient(script)
        monkeypatch.setattr(runner, "_agentic_server", _FakeServer(results))
        monkeypatch.setattr("kb_mcp.llm.get_openai_client", lambda model=None: client)
        answer, _, trace, _ = runner._agentic_answer("q?", model="m", source_document_id=SOURCE, **kwargs)
        return answer, trace, client

    return _run


def test_tool_result_is_truncated_and_labelled(run_agent):
    answer, trace, client = run_agent(
        [_tool_call_reply(), _text_reply("done")], {"kb_search": "x" * 500}, tool_result_max_chars=100
    )
    assert answer == "done"
    tool_msg = next(m for m in client.requests[1]["messages"] if isinstance(m, dict) and m.get("role") == "tool")
    assert tool_msg["content"].startswith("x" * 100)
    assert "TRUNCATED BY CLIENT: showing 100 of 500 chars" in tool_msg["content"]
    assert trace[0]["truncated"] is True and trace[0]["result_len"] == 500


def test_server_prompts_reach_the_model(run_agent):
    _, _, client = run_agent([_text_reply("done")], {})
    messages = client.requests[0]["messages"]
    assert messages[0] == {"role": "system", "content": "server instructions"}
    assert messages[1] == {"role": "user", "content": "Research: q?"}


def test_source_seen_in_search_but_not_opened(run_agent):
    _, trace, _ = run_agent([_tool_call_reply(), _text_reply("done")], {"kb_search": f"ID: {SOURCE}"})
    summary = trace[-1]
    assert summary["saw_source"] is True
    assert summary["opened_source"] is False


def test_turn_limit_forces_an_answer_without_tools(run_agent):
    answer, trace, client = run_agent(
        [_tool_call_reply(), _tool_call_reply(), _text_reply("forced")], {"kb_search": "r"}, max_turns=2
    )
    assert answer == "forced"
    assert client.requests[-1]["tool_choice"] == "none"
    assert trace[-1]["hit_turn_limit"] is True


def test_context_overflow_is_recorded_not_raised(run_agent):
    overflow = openai.BadRequestError(
        "maximum context length exceeded",
        response=httpx.Response(400, request=httpx.Request("POST", "http://x")),
        body=None,
    )
    answer, trace, client = run_agent([_tool_call_reply(), overflow], {"kb_search": "r"})
    assert answer == ""
    assert "maximum context length" in trace[-1]["stopped_by"]
    assert trace[-1]["hit_turn_limit"] is False
    assert len(client.requests) == 2  # no forced final call after a rejection


def test_limits_come_from_env_unless_given(run_agent, monkeypatch):
    monkeypatch.setenv("EVAL_AGENTIC_TOOL_RESULT_MAX_CHARS", "50")
    monkeypatch.setenv("EVAL_AGENTIC_MAX_TURNS", "1")
    _, trace, client = run_agent([_tool_call_reply(), _text_reply("forced")], {"kb_search": "x" * 80})
    assert trace[0]["truncated"] is True
    assert trace[-1]["hit_turn_limit"] is True  # one round, then the forced answer
    # An explicit argument (the CLI flag, via the run's meta) beats the env.
    _, trace, _ = run_agent([_tool_call_reply(), _text_reply("done")], {"kb_search": "x" * 80},
                            tool_result_max_chars=1000, max_turns=5)
    assert trace[0]["truncated"] is False
    assert trace[-1]["hit_turn_limit"] is False
