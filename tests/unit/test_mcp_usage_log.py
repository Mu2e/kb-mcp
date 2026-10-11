"""Counting MCP tool calls: the wrapper, what it records (and does not), and the summary."""

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.server import usage_log
from kb_mcp.server.usage_log import Context, logged


@pytest.fixture()
def rows(monkeypatch):
    recorded = []
    monkeypatch.setattr(usage_log, "record", recorded.append)
    return recorded


def fake_ctx(session_id="sess-1"):
    request = SimpleNamespace(headers={"mcp-session-id": session_id})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def test_wrapper_keeps_the_tools_parameters_and_answer(rows):
    def kb_get(identifier: str, max_chars: int = 5) -> str:
        """Doc."""
        return f"text of {identifier}"

    wrapped = logged(kb_get)
    visible = [p for p in inspect.signature(wrapped).parameters if p != usage_log._CTX_PARAM]
    assert visible == ["identifier", "max_chars"] and wrapped.__doc__ == "Doc."
    assert wrapped("doc-1") == "text of doc-1"
    [row] = rows
    assert row["tool"] == "kb_get" and row["status"] == "ok" and row["response_chars"] == len("text of doc-1")
    assert row["transport"] == "stdio" and row["session_id"] is None


def test_only_usage_fields_are_recorded(rows):
    """No user, no arguments, no error text: usage statistics only."""
    def kb_search(query: str) -> str:
        return "ERROR: something about 'secret query'"

    logged(kb_search)("secret query", usage_ctx=fake_ctx())
    [row] = rows
    assert set(row) == {"created_time", "tool", "session_id", "transport", "status", "error_kind",
                        "duration_ms", "response_chars"}
    assert "secret" not in repr(row)
    assert (row["status"], row["error_kind"], row["session_id"], row["transport"]) == \
        ("error", "error answer", "sess-1", "http")


@pytest.mark.parametrize("answer, error", [
    ('{"error": "Invalid search_filter"}', True),
    ('{"message": "No results found", "results": []}', False),
    ("[[DOCUMENT_METADATA]] ERROR is a word in the text", False),
    ("ERROR: Document not found", True),
])
def test_error_answers_are_recognised(answer, error):
    assert usage_log.is_error_answer(answer) is error


def test_exceptions_are_recorded_by_class_and_reraised(rows):
    def kb_get_image(identifier: str) -> str:
        raise ValueError(f"Image not found: {identifier}")

    with pytest.raises(ValueError):
        logged(kb_get_image)("mu2e-docdb/123-private.png")
    [row] = rows
    assert row["status"] == "error" and row["error_kind"] == "ValueError" and row["response_chars"] is None
    assert "private" not in repr(row)


def test_a_tool_with_its_own_context_gets_it(rows):
    seen = {}

    async def kb_research(question: str, ctx: Context = None) -> str:
        seen["ctx"] = ctx
        return "answer"

    wrapped = logged(kb_research)
    assert usage_log._CTX_PARAM not in inspect.signature(wrapped).parameters  # reuses ctx
    ctx = fake_ctx("sess-9")
    assert asyncio.run(wrapped("why?", ctx=ctx)) == "answer"
    assert seen["ctx"] is ctx and rows[0]["session_id"] == "sess-9"


def test_a_failing_database_write_is_swallowed(monkeypatch):
    import kb_mcp.kb.database as database

    def broken_session(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(database, "get_db_session", broken_session)
    usage_log._write({"tool": "kb_search", "status": "ok"})  # must not raise


# --- summary ---------------------------------------------------------------------------

@pytest.fixture()
def session(tmp_path):
    from kb_mcp.kb.db_models import Base, McpCallLog

    engine = create_engine(f"sqlite:///{tmp_path}/usage.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)
    calls = [
        ("kb_search", "s1", "ok", 100, 1), ("kb_search", "s1", "ok", 300, 1), ("kb_get", "s1", "ok", 20, 1),
        ("kb_search", "s2", "error", 50, 2), ("kb_get_image", "s2", "ok", 10, 2),
        ("kb_search", None, "ok", 200, 3),          # stdio, no session
        ("kb_get", "s3", "ok", 30, 90),             # outside a 30-day window
    ]
    for tool, sid, status, ms, days_ago in calls:
        s.add(McpCallLog(tool=tool, session_id=sid, transport="http" if sid else "stdio", status=status,
                         duration_ms=ms, created_time=now - timedelta(days=days_ago)))
    s.commit()
    yield s
    s.close()


def test_usage_summary_counts_tools_sessions_and_errors(session):
    from kb_mcp.kb.logs import get_mcp_usage

    usage = get_mcp_usage(days=30, session=session)
    by_tool = {t["tool"]: t for t in usage["tools"]}
    assert [t["tool"] for t in usage["tools"]][0] == "kb_search"
    assert (by_tool["kb_search"]["calls"], by_tool["kb_search"]["sessions"], by_tool["kb_search"]["errors"]) == (4, 2, 1)
    assert by_tool["kb_search"]["median_ms"] == 200
    assert "kb_get" in by_tool and by_tool["kb_get"]["calls"] == 1     # the 90-day-old call is out
    assert usage["totals"] == {"calls": 6, "sessions": 2, "by_transport": {"http": 5, "stdio": 1}}
    s1 = next(s for s in usage["sessions"] if s["session_id"] == "s1")
    assert s1["calls"] == 3 and s1["tools"] == {"kb_search": 2, "kb_get": 1}
    assert get_mcp_usage(days=None, session=session)["totals"]["calls"] == 7


def test_call_time_is_stamped_by_the_wrapper(rows):
    # Not by the database when the background writer catches up.
    from datetime import datetime, timedelta, timezone

    def kb_get(identifier: str) -> str:
        return "text"

    before = datetime.now(timezone.utc)
    logged(kb_get)("x", usage_ctx=fake_ctx())
    [row] = rows
    assert before - timedelta(seconds=1) <= row["created_time"] <= datetime.now(timezone.utc)
