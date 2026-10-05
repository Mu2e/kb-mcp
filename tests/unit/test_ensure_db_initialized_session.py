"""_ensure_db_initialized must never touch the global engine when a caller
passes its own session.

init_db() prepares the globally *configured* engine (get_engine(), from
whatever .env/KB_ENV_FILE the process resolves) -- not any particular
session object. Before this fix, every get()/add_document()/etc. call ran
_ensure_db_initialized() unconditionally on first use in a process,
regardless of whether the caller had passed an explicit `session` bound to
a completely different (even unrelated) engine -- e.g. an isolated test
database. In a checkout whose real .env points at a production database,
this meant calling get(uid=..., session=my_own_sqlite_session) still tried
to reach the real production database as a side effect: CREATE EXTENSION,
create_all, and schema-patching, over the network, every single time, with
no way for the caller to opt out short of it already having run once
earlier in the process.
"""

import kb_mcp.kb.documents.operations as ops


def test_no_session_still_initializes_the_global_engine_once(monkeypatch):
    """The common case (CLI, server, no explicit session) is unchanged: the
    function is about to open its own session from the global engine via
    get_db_session(), which is exactly what init_db() must prepare first."""
    monkeypatch.setattr(ops, "_db_initialized", False)
    calls = []
    monkeypatch.setattr(ops, "init_db", lambda create_tables=True: calls.append(create_tables))

    ops._ensure_db_initialized(session=None)
    assert calls == [True]

    # Second call in the same process: already initialized, must not repeat.
    ops._ensure_db_initialized(session=None)
    assert calls == [True]


def test_an_explicit_session_skips_the_global_engine_entirely(monkeypatch):
    """The bug: a caller-provided session must never trigger init_db()
    against the unrelated globally configured engine, regardless of whether
    the global flag has tripped yet in this process."""
    monkeypatch.setattr(ops, "_db_initialized", False)
    calls = []
    monkeypatch.setattr(ops, "init_db", lambda create_tables=True: calls.append(create_tables))

    ops._ensure_db_initialized(session=object())

    assert calls == [], "init_db() must not run when the caller supplied its own session"
    # And it must not have consumed the one-time global flag either -- a
    # later call with no session still needs to do the real work.
    assert ops._db_initialized is False


def test_a_later_call_with_no_session_still_initializes_after_a_session_call(monkeypatch):
    monkeypatch.setattr(ops, "_db_initialized", False)
    calls = []
    monkeypatch.setattr(ops, "init_db", lambda create_tables=True: calls.append(create_tables))

    ops._ensure_db_initialized(session=object())
    ops._ensure_db_initialized(session=None)

    assert calls == [True]
