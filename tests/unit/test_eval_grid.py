"""Unit tests for eval grid naming and run attribution of LLM usage."""

from contextlib import contextmanager

import kb_mcp.kb.database as database
import kb_mcp.kb.db_models as db_models
from kb_mcp.kb.eval.grid import grid_run_name, model_label
from kb_mcp.llm.usage import record_llm_usage, usage_context


def test_model_labels_are_short_and_name_safe():
    assert model_label("argo:claude-sonnet-5") == "claude-sonnet-5"
    assert model_label("gpt-oss:120b") == "gpt-oss-120b"
    assert model_label("google/gemma-4-31B-it") == "google-gemma-4-31B-it"
    assert model_label(None) == "default"


def test_run_names_encode_what_was_tested():
    assert grid_run_name("pilot", "agentic", "gpt-5.5", "argo:claude-opus-5", "argo:gpt-5.5") == \
        "pilot-agentic-claude-opus-5-qgpt-5.5-judge-gpt-5.5"
    # Retrieval-only runs have no answering model in their name.
    assert grid_run_name("pilot", "hybrid", "gpt-oss-120b", "argo:claude-opus-5") == "pilot-hybrid-qgpt-oss-120b"


def test_usage_rows_carry_the_context(monkeypatch):
    written = []

    class _Session:
        def add(self, row):
            written.append(row)

    @contextmanager
    def _session(**kwargs):
        yield _Session()

    monkeypatch.setattr(database, "get_db_session", _session)
    monkeypatch.setattr(db_models, "LLMUsage", lambda **kw: kw)

    with usage_context(eval_run_id="run-1", eval_question_id="q-1"):
        record_llm_usage({"prompt_tokens": 3, "completion_tokens": 2}, stage="eval_judge", meta={"mode": "x"})
    record_llm_usage({"prompt_tokens": 1}, stage="eval_judge")

    assert written[0]["meta"] == {"eval_run_id": "run-1", "eval_question_id": "q-1", "mode": "x"}
    assert written[1]["meta"] == {}  # context ends with the block


def test_rejudge_refuses_retrieval_runs(monkeypatch):
    from types import SimpleNamespace

    import pytest

    import kb_mcp.kb.eval.rejudge as rejudge

    @contextmanager
    def _session(*a, **k):
        yield SimpleNamespace(get=lambda model, rid: SimpleNamespace(id=rid, search_type="hybrid", name="x"))

    monkeypatch.setattr(rejudge, "get_db_session", _session)
    with pytest.raises(ValueError, match="only answer-mode runs"):
        rejudge.rejudge_run("r1", "argo:claude-opus-5")
