"""End-to-end record_visibility_decision behavior against a real Postgres.

record_visibility_decision relies on two things SQLite cannot provide: a
real SEQUENCE for DocumentVisibilityDecision.seq, and ON CONFLICT ... DO
UPDATE ... WHERE for the ordering-safe upsert into
document_visibility_current. The statement shape is pinned without a
database in test_document_visibility_upsert_sql.py; this file proves
Postgres actually enforces it at runtime -- the same scenario manually
verified live against the dev tunnel while building this feature: two
ordered decisions resolve correctly, and a decision issued earlier (lower
seq) cannot clobber one issued later (higher seq) even if it is written
after, which is the scenario concurrent sweep workers could hit.

Uses the project's existing engine/tables/db_session fixtures
(tests/conftest.py), which already default to SQLite and skip only on a
connection failure -- not on dialect. Skips explicitly here instead when the
resolved engine isn't Postgres, so these tests are silent by default and
only run with TEST_DATABASE_URL pointed at a real Postgres instance.
"""

import pytest

from kb_mcp.kb.db_models import RawDocument, Source, DocumentVisibilityCurrent
from kb_mcp.kb.tools import record_visibility_decision


@pytest.fixture(autouse=True)
def _require_postgres(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip(
            "needs a real Postgres (record_visibility_decision uses a SEQUENCE "
            "and ON CONFLICT ... DO UPDATE ... WHERE); set TEST_DATABASE_URL"
        )


@pytest.fixture()
def raw_document_id(db_session):
    db_session.add(Source(id="test-visibility-source"))
    raw = RawDocument(
        id="test-visibility-raw-doc", source_id="test-visibility-source",
        source_type="application/pdf", content_hash="deadbeef",
    )
    db_session.add(raw)
    db_session.flush()
    return raw.id


def _current(db_session, raw_document_id):
    return db_session.query(DocumentVisibilityCurrent).filter_by(
        raw_document_id=raw_document_id
    ).first()


def test_a_single_decision_resolves_into_current(db_session, raw_document_id):
    r = record_visibility_decision(
        raw_document_id, visible_search=False, visible_get=True,
        reason="oversized", session=db_session,
    )
    assert r["applied"] is True

    cur = _current(db_session, raw_document_id)
    assert cur.visible_search is False
    assert cur.visible_get is True
    assert cur.decision_seq == r["seq"]


def test_a_later_decision_overrides_an_earlier_one(db_session, raw_document_id):
    r1 = record_visibility_decision(
        raw_document_id, visible_search=False, visible_get=True,
        reason="oversized", session=db_session,
    )
    r2 = record_visibility_decision(
        raw_document_id, visible_search=True, visible_get=True,
        reason="reviewed, keep visible", session=db_session,
    )
    assert r2["seq"] > r1["seq"]

    cur = _current(db_session, raw_document_id)
    assert cur.visible_search is True
    assert cur.reason == "reviewed, keep visible"
    assert cur.decision_seq == r2["seq"]


def test_a_decision_issued_earlier_cannot_clobber_one_issued_later(db_session, raw_document_id):
    """The scenario the seq guard exists for: two decisions race for the same
    key, and the one with the lower seq is written to document_visibility_current
    second (e.g. its transaction was slower to commit). It must lose."""
    r1 = record_visibility_decision(
        raw_document_id, visible_search=False, visible_get=True,
        reason="first, lower seq", session=db_session,
    )
    r2 = record_visibility_decision(
        raw_document_id, visible_search=True, visible_get=True,
        reason="second, higher seq -- already applied", session=db_session,
    )

    from kb_mcp.kb.tools import _build_visibility_current_upsert
    stale = _build_visibility_current_upsert(
        raw_document_id=raw_document_id, group_name="*",
        visible_search=False, visible_get=False,
        reason="STALE -- must be rejected",
        decision_id=r1["decision_id"], decision_seq=r1["seq"],
    )
    result = db_session.execute(stale)
    assert result.rowcount == 0

    cur = _current(db_session, raw_document_id)
    assert cur.reason == "second, higher seq -- already applied"
    assert cur.decision_seq == r2["seq"]


def test_no_row_means_visible_until_a_decision_is_recorded(db_session, raw_document_id):
    assert _current(db_session, raw_document_id) is None


def test_already_decided_is_scoped_to_the_group(db_session, raw_document_id):
    from kb_mcp.kb.tools import _already_decided

    record_visibility_decision(
        raw_document_id, visible_search=True, visible_get=True,
        reason="reviewed", session=db_session,
    )
    assert _already_decided(db_session, {raw_document_id}, "*") == {raw_document_id}
    assert _already_decided(db_session, {raw_document_id}, "some-group") == set()
