"""Compile-only checks on the document_visibility_current upsert statement.

_build_visibility_current_upsert is a pure function (no session, no flush) so
its exact SQL -- in particular the ordering guard in WHERE -- can be compiled
and inspected without a database, the same way test_search_filters.py tests
filter-fragment compilation. The guard itself (does Postgres actually reject
a stale write at runtime) is proven live in test_document_visibility_postgres.py;
these tests pin the statement shape that guard depends on.
"""

from sqlalchemy.dialects import postgresql

from kb_mcp.kb.tools import _build_visibility_current_upsert


def _compile(stmt):
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _build(**overrides):
    kwargs = dict(
        raw_document_id="raw-1",
        group_name="*",
        visible_search=False,
        visible_get=True,
        reason="too large",
        decision_id="dec-1",
        decision_seq=5,
    )
    kwargs.update(overrides)
    return _build_visibility_current_upsert(**kwargs)


def test_it_is_an_upsert_on_the_composite_key():
    sql = _compile(_build())
    assert "INSERT INTO document_visibility_current" in sql
    assert "ON CONFLICT (raw_document_id, group_name) DO UPDATE" in sql


def test_the_guard_accepts_an_empty_current_row():
    """No current row for this key (decision_seq IS NULL) must never block the write."""
    sql = _compile(_build())
    assert "document_visibility_current.decision_seq IS NULL" in sql


def test_the_guard_compares_against_the_incoming_seq_not_a_fixed_value():
    """Ties the guard to EXCLUDED.decision_seq -- this decision's own seq --
    not some other column, so a second call with a higher seq is the only
    thing that can ever overwrite a given seq."""
    sql = _compile(_build(decision_seq=5))
    assert "document_visibility_current.decision_seq < excluded.decision_seq" in sql


def test_a_lower_seq_literal_still_compiles_the_same_guard_shape():
    """The guard is structural (a column comparison), not baked from the
    literal value passed in -- changing decision_seq must not change which
    comparison is emitted, only the value EXCLUDED resolves to at runtime."""
    low = _compile(_build(decision_seq=1))
    high = _compile(_build(decision_seq=999))
    # Only the VALUES clause differs; the ON CONFLICT guard clause is identical.
    guard = "WHERE document_visibility_current.decision_seq IS NULL OR document_visibility_current.decision_seq < excluded.decision_seq"
    assert guard in low
    assert guard in high


def test_every_value_column_is_set_from_excluded_not_the_old_row():
    """Latest-decision-wins, not a merge: every value column takes the new
    decision's value outright (see DocumentVisibilityCurrent's docstring on
    why this isn't a ratchet toward the more restrictive value)."""
    sql = _compile(_build())
    for col in ("visible_search", "visible_get", "reason", "decision_id", "decision_seq"):
        assert f"{col} = excluded.{col}" in sql, col


def test_updated_time_is_set_to_now_not_a_passed_in_value():
    sql = _compile(_build())
    assert "updated_time = now()" in sql
