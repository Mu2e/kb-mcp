"""_ensure_db_initialized must not touch the global engine for a session bound elsewhere.

init_db() prepares the globally *configured* engine (get_engine(), from
whatever .env/KB_ENV_FILE the process resolves) -- not any particular
session. It used to run on first use in a process regardless of the session
a caller passed, so get(uid=..., session=my_own_sqlite_session) in a checkout
whose .env points at production still reached out to production
(CREATE EXTENSION, create_all, schema patching) as a side effect.

A session opened from the global engine itself must still trigger the lazy
init: web routes pass their own such sessions and the servers never call
init_db() at startup.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import kb_mcp.kb.documents.operations as ops


@pytest.fixture()
def init_calls(monkeypatch):
    global_engine = create_engine("sqlite://")
    monkeypatch.setattr(ops, "get_engine", lambda: global_engine)
    monkeypatch.setattr(ops, "_db_initialized", False)
    calls = []
    monkeypatch.setattr(ops, "init_db", lambda create_tables=True: calls.append(create_tables))
    return global_engine, calls


def test_no_session_initializes_the_global_engine_once(init_calls):
    _, calls = init_calls
    ops._ensure_db_initialized(session=None)
    ops._ensure_db_initialized(session=None)
    assert calls == [True]


def test_a_session_on_another_engine_skips_init(init_calls):
    _, calls = init_calls
    other = Session(bind=create_engine("sqlite://"))
    ops._ensure_db_initialized(session=other)
    assert calls == []
    # The one-time flag must stay untripped for a later global-engine call.
    assert ops._db_initialized is False


def test_a_session_on_another_engines_connection_skips_init(init_calls):
    """Test fixtures bind sessions to a Connection, not an Engine."""
    _, calls = init_calls
    conn = create_engine("sqlite://").connect()
    ops._ensure_db_initialized(session=Session(bind=conn))
    conn.close()
    assert calls == []


def test_a_session_on_the_global_engine_still_initializes(init_calls):
    global_engine, calls = init_calls
    ops._ensure_db_initialized(session=Session(bind=global_engine))
    assert calls == [True]


def test_a_later_call_with_no_session_still_initializes(init_calls):
    _, calls = init_calls
    ops._ensure_db_initialized(session=Session(bind=create_engine("sqlite://")))
    ops._ensure_db_initialized(session=None)
    assert calls == [True]
