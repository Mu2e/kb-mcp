"""Candidate selection for `kb tools visibility sweep`, against ephemeral SQLite.

The rule functions and the already-decided check are plain ORM queries, so
they run here with an explicit session. Writing decisions (--apply) goes
through record_visibility_decision, which needs Postgres and is covered by
test_document_visibility_postgres.py.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, RawDocument, Source
from kb_mcp.kb.tools import (
    SPREADSHEET_SOURCE_TYPES,
    VISIBILITY_RULES,
    _already_decided,
    _rule_oversized_spreadsheet,
)

XLSX = SPREADSHEET_SOURCE_TYPES[0]
PDF = "application/pdf"


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/sweep.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Source(id="mu2e-docdb"), Source(id="mu2e-wiki")])
    yield s
    s.close()


def _add(session, raw_id, source_type, chars, source_id="mu2e-docdb", extra_docs=()):
    session.add(RawDocument(id=raw_id, source_id=source_id, source_type=source_type, content_hash=raw_id))
    session.add(Document(id=f"{raw_id}-doc", source_id=source_id, raw_document_id=raw_id,
                         source_type=source_type, text="x" * chars))
    for i, n in enumerate(extra_docs):
        session.add(Document(id=f"{raw_id}-child{i}", source_id=source_id, raw_document_id=raw_id,
                             source_type=source_type, doc_type="table", text="y" * n))
    session.flush()


def test_rule_is_registered():
    assert VISIBILITY_RULES["oversized-spreadsheet"] is _rule_oversized_spreadsheet


def test_selects_only_spreadsheets_over_the_threshold(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    _add(session, "small-xlsx", XLSX, 500)
    _add(session, "big-pdf", PDF, 5_000)  # long reports are not the target
    got = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert [d["raw_document_id"] for d in got] == ["big-xlsx"]


def test_default_hides_from_search_only(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    (d,) = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert d["visible_search"] is False
    assert d["visible_get"] is True
    assert d["meta"]["chars"] == 2_000 and d["meta"]["rule"] == "oversized-spreadsheet"


def test_hide_get_also_hides_from_get(session):
    _add(session, "big-xlsx", XLSX, 2_000)
    (d,) = _rule_oversized_spreadsheet(session, max_chars=1_000, hide_get=True)
    assert d["visible_get"] is False


def test_one_decision_per_raw_document_using_its_longest_text(session):
    """A raw file's extracted tables share its raw_document_id; it must
    still come out once, sized by its longest parsed text."""
    _add(session, "big-xlsx", XLSX, 500, extra_docs=(3_000, 200))
    got = _rule_oversized_spreadsheet(session, max_chars=1_000)
    assert len(got) == 1 and got[0]["meta"]["chars"] == 3_000


def test_source_id_restricts(session):
    _add(session, "docdb-xlsx", XLSX, 2_000, source_id="mu2e-docdb")
    _add(session, "wiki-xlsx", XLSX, 2_000, source_id="mu2e-wiki")
    got = _rule_oversized_spreadsheet(session, source_id="mu2e-wiki", max_chars=1_000)
    assert [d["raw_document_id"] for d in got] == ["wiki-xlsx"]


def test_already_decided_with_no_decisions(session):
    """Rows in document_visibility_decisions need a real Postgres sequence for
    seq, so only the empty cases run here; group scoping and the skip itself
    are in test_document_visibility_postgres.py."""
    assert _already_decided(session, set(), "*") == set()
    assert _already_decided(session, {"nothing-decided"}, "*") == set()
