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


def test_entry_key_groups_files_versions_and_figures_of_one_docdb_entry():
    key = runner.document_entry_key
    same = {
        key("mu2e-docdb", "55441-STM_US_Proc_Spec_Trolley_Assy_V3_docx"),
        key("mu2e-docdb", "55441-STM_US_Proc_Spec_Trolley_Assy_V3_pdf"),
        key("mu2e-docdb", "55441/STM_US_Proc_Spec_Trolley_Assy_V2"),
        key("mu2e-docdb", "55441-STM_US_Proc_Spec_Trolley_Assy_V3_pdf-_page_4_Figure_0.png"),
    }
    assert same == {"mu2e-docdb:55441"}
    assert key("mu2e-docdb", "56095") == "mu2e-docdb:56095"
    assert key("mu2e-docdb", "554410-other") != key("mu2e-docdb", "55441-x")
    assert key("other-source", "55441-x") != key("mu2e-docdb", "55441-x")
    # No leading entry number: the doc id is its own entry.
    assert key("mu2e-wiki", "Tracker/Straws") == "mu2e-wiki:Tracker/Straws"


def test_source_is_recognised_by_entry_in_tool_results():
    key = "mu2e-docdb:52728"
    kb_get = "[[DOCUMENT_METADATA]]\nTitle: STM drawings\nID: 52728-F10266664_STM_DS_Inner_Frame\nSource: mu2e-docdb\n"
    kb_search = '{"results": [{"source_id": "mu2e-docdb", "doc_id": "52728-other_file_pdf-_page_2_Figure_1.png"}]}'
    elsewhere = '{"results": [{"doc_id": "55441-STM_US_Proc_Spec"}]}\nID: 5272-unrelated'
    assert runner.mentions_source(kb_get, source_entry_key=key)
    assert runner.mentions_source(kb_search, source_entry_key=key)
    assert not runner.mentions_source(elsewhere, source_entry_key=key)
    assert runner.mentions_source(f"ID: {SOURCE}", source_document_id=SOURCE)
