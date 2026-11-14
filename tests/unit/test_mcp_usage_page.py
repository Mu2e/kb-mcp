"""The public MCP usage page: daily counts per tool and sessions, nothing identifying."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.applications import Starlette
from starlette.testclient import TestClient

from kb_mcp.kb.logs import get_mcp_usage_daily
from kb_mcp.server.web.routes import usage as usage_routes

UTC = timezone.utc


@pytest.fixture()
def session(tmp_path):
    from kb_mcp.kb.db_models import Base, McpCallLog

    engine = create_engine(f"sqlite:///{tmp_path}/usage.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    now = datetime.now(UTC)
    calls = [  # (tool, session, status, ms, hours ago)
        ("kb_search", "secret-session-1", "ok", 100, 1), ("kb_search", "secret-session-1", "ok", 300, 1),
        ("kb_get", "secret-session-1", "ok", 20, 1),
        ("kb_search", "secret-session-2", "error", 50, 26), ("kb_get_image", "secret-session-2", "ok", 10, 26),
        ("kb_search", None, "ok", 200, 26),                      # stdio: no session
        ("kb_get", "secret-session-3", "ok", 30, 24 * 45),       # outside a 30-day window
    ]
    for tool, sid, status, ms, hours in calls:
        s.add(McpCallLog(tool=tool, session_id=sid, transport="http" if sid else "stdio", status=status,
                         duration_ms=ms, created_time=now - timedelta(hours=hours)))
    s.commit()
    yield s
    s.close()


def test_daily_series_counts_calls_per_tool_and_sessions(session):
    d = get_mcp_usage_daily(days=30, tz="UTC", session=session)
    assert d["days"] == 30 and len(d["series"]) == 30, "one entry per day, empty days included"
    assert d["series"][-1]["date"] == datetime.now(UTC).date().isoformat(), "the last day is today"
    assert d["totals"]["calls"] == 6 and d["totals"]["sessions"] == 2 and d["totals"]["errors"] == 1
    assert d["totals"]["by_tool"] == {"kb_search": 4, "kb_get": 1, "kb_get_image": 1}
    assert d["tools"][0] == "kb_search", "busiest tool first"
    assert sum(day["total"] for day in d["series"]) == 6
    assert d["latency_ms"]["kb_search"]["median"] == 200
    busy = [day for day in d["series"] if day["total"]]
    assert all(set(day) == {"date", "calls", "total", "sessions", "errors"} for day in d["series"])
    assert sum(day["sessions"] for day in busy) >= 2


def test_nothing_identifying_leaves_the_function(session):
    blob = json.dumps(get_mcp_usage_daily(days=90, tz="UTC", session=session))
    assert "secret-session" not in blob
    assert get_mcp_usage_daily(days=90, tz="UTC", session=session)["totals"]["calls"] == 7


def test_days_are_bounded_and_an_unknown_zone_falls_back_to_utc(session):
    assert get_mcp_usage_daily(days=0, tz="UTC", session=session)["days"] == 1
    assert get_mcp_usage_daily(days=10_000, tz="UTC", session=session)["days"] == 366
    assert get_mcp_usage_daily(days=7, tz="Not/AZone", session=session)["timezone"] == "UTC"


@pytest.mark.parametrize("value, expected", [("7", 7), ("30", 30), ("90", 90), ("365", 30), ("x", 30), (None, 30)])
def test_only_the_offered_ranges_are_accepted(value, expected):
    assert usage_routes.parse_days(value) == expected


def _client(monkeypatch, data):
    async def open_html(request, session_manager, **_):
        return {}, None

    async def open_api(request, session_manager, **_):
        return {}, None

    import kb_mcp.kb.logs as logs
    seen = {}

    def fake(days, *a, **k):
        seen["days"] = days
        return data

    monkeypatch.setattr(logs, "get_mcp_usage_daily", fake)
    app = Starlette()
    usage_routes.setup_usage_routes(app, None, open_html, open_api)
    return TestClient(app), seen


def test_routes_serve_the_page_and_the_counts(monkeypatch):
    data = {"days": 7, "timezone": "UTC", "tools": [], "series": [], "latency_ms": {},
            "totals": {"calls": 0, "sessions": 0, "errors": 0, "by_tool": {}}}
    client, seen = _client(monkeypatch, data)
    r = client.get("/api/usage?days=7")
    assert r.status_code == 200 and r.json() == data and seen["days"] == 7
    page = client.get("/web/usage")
    assert page.status_code == 200 and "MCP Usage" in page.text and "/api/usage" in page.text


def test_the_page_says_what_it_does_not_show(monkeypatch):
    client, _ = _client(monkeypatch, {})
    assert "no users, queries or session identifiers" in client.get("/web/usage").text
