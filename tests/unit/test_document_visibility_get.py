"""enforce_visibility behavior of kb_mcp.kb.get(), against ephemeral SQLite.

get()'s visibility check only reads document_visibility_current (a plain
Boolean column, no Postgres-only features), so unlike record_visibility_decision
this can run end-to-end against SQLite without a real Postgres connection.

Default (enforce_visibility=False) must stay unaffected: this is what every
internal/admin caller (CLI, web admin routes, eval, dedup, ...) uses, and
none of them should need updating for a hidden document to keep showing up.
Only the agent-facing MCP entry points (kb_get, kb_get_image, the
kb://document resource) pass enforce_visibility=True.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, RawDocument, Source, DocumentVisibilityCurrent
from kb_mcp.kb.documents.operations import get


@pytest.fixture()
def session(tmp_path, monkeypatch):
    # get() lazily calls _ensure_db_initialized() on first use, which -- even
    # though we pass our own session explicitly below -- initializes the
    # separate, globally *configured* engine (whatever .env/KB_ENV_FILE this
    # checkout resolves to) as a side effect. This fixture already creates
    # every table this test needs on its own throwaway engine, so suppress
    # that lazy init rather than let it reach out to a real configured DB.
    import kb_mcp.kb.documents.operations as ops
    monkeypatch.setattr(ops, "_db_initialized", True)

    engine = create_engine(f"sqlite:///{tmp_path}/get_visibility.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture()
def two_documents(session):
    """One raw document with visible and hidden parsed Document rows.

    `visible_doc` and `hidden_doc` share no raw document -- each gets its
    own, since visibility is keyed on raw_document_id and a shared one would
    make both hidden together.
    """
    source = Source(id="mu2e-docdb")
    session.add(source)

    raw_visible = RawDocument(id="raw-visible", source_id="mu2e-docdb", source_type="application/pdf", content_hash="h1")
    raw_hidden = RawDocument(id="raw-hidden", source_id="mu2e-docdb", source_type="application/pdf", content_hash="h2")
    session.add_all([raw_visible, raw_hidden])

    visible_doc = Document(id="doc-visible", source_id="mu2e-docdb", raw_document_id="raw-visible", source_type="application/pdf", text="visible content")
    hidden_doc = Document(id="doc-hidden", source_id="mu2e-docdb", raw_document_id="raw-hidden", source_type="application/pdf", text="hidden content")
    session.add_all([visible_doc, hidden_doc])

    session.add(DocumentVisibilityCurrent(
        raw_document_id="raw-hidden", group_name="*",
        visible_search=True, visible_get=False,
    ))
    session.flush()
    return visible_doc, hidden_doc


def test_default_sees_both_documents(session, two_documents):
    visible_doc, hidden_doc = two_documents
    assert get(uid=visible_doc.id, session=session) is not None
    assert get(uid=hidden_doc.id, session=session) is not None


def test_enforced_hides_only_the_hidden_one(session, two_documents):
    visible_doc, hidden_doc = two_documents
    assert get(uid=visible_doc.id, session=session, enforce_visibility=True) is not None
    assert get(uid=hidden_doc.id, session=session, enforce_visibility=True) is None


def test_enforced_batch_drops_the_hidden_one_keeps_the_visible_one(session, two_documents):
    visible_doc, hidden_doc = two_documents
    result = get(uid=[visible_doc.id, hidden_doc.id], session=session, enforce_visibility=True)
    assert result is not None
    ids = {d.id for d in result}
    assert ids == {visible_doc.id}


def test_enforced_batch_of_only_hidden_docs_returns_none(session, two_documents):
    _, hidden_doc = two_documents
    assert get(uid=[hidden_doc.id], session=session, enforce_visibility=True) is None


def test_a_document_with_no_raw_document_id_is_never_hidden(session):
    """Not all documents come from raw files -- there is no key to look up a
    visibility exception for, so these must always pass through."""
    source = Source(id="mu2e-docdb")
    session.add(source)
    doc = Document(id="doc-generated", source_id="mu2e-docdb", raw_document_id=None, source_type="text/plain", text="synthetic")
    session.add(doc)
    session.flush()

    assert get(uid="doc-generated", session=session, enforce_visibility=True) is not None


def test_visible_get_true_row_does_not_hide_the_document(session):
    """An explicit visible_get=True row (recorded so a sweep stops
    re-flagging a reviewed document) must behave exactly like no row at all."""
    source = Source(id="mu2e-docdb")
    session.add(source)
    raw = RawDocument(id="raw-reviewed", source_id="mu2e-docdb", source_type="application/pdf", content_hash="h3")
    session.add(raw)
    doc = Document(id="doc-reviewed", source_id="mu2e-docdb", raw_document_id="raw-reviewed", source_type="application/pdf", text="reviewed, kept visible")
    session.add(doc)
    session.add(DocumentVisibilityCurrent(
        raw_document_id="raw-reviewed", group_name="*",
        visible_search=True, visible_get=True,
    ))
    session.flush()

    assert get(uid="doc-reviewed", session=session, enforce_visibility=True) is not None
