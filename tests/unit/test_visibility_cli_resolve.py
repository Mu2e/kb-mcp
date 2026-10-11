"""Identifier resolution for `kb tools visibility hide|show|history`.

Visibility is decided per raw file, so every accepted form must land on
exactly one RawDocument: its UUID, a parsed document's UUID, source_id/doc_id,
or a bare doc_id. An ambiguous doc_id must fail loudly rather than pick one.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.cli.tools_commands import _resolve_raw_document
from kb_mcp.kb.db_models import Base, Document, RawDocument, Source


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/resolve.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([Source(id="mu2e-docdb"), Source(id="mu2e-wiki")])
    s.add_all([
        RawDocument(id="raw-a", source_id="mu2e-docdb", doc_id="100-a", source_type="application/pdf", content_hash="1"),
        RawDocument(id="raw-dup-1", source_id="mu2e-docdb", doc_id="200-dup", source_type="application/pdf", content_hash="2"),
        RawDocument(id="raw-dup-2", source_id="mu2e-wiki", doc_id="200-dup", source_type="text/html", content_hash="3"),
    ])
    s.add_all([
        Document(id="doc-a", source_id="mu2e-docdb", raw_document_id="raw-a", source_type="application/pdf", doc_id="100-a"),
        Document(id="doc-orphan", source_id="mu2e-docdb", raw_document_id=None, source_type="text/plain", doc_id="gen"),
    ])
    s.flush()
    yield s
    s.close()


def test_raw_document_uuid(session):
    assert _resolve_raw_document(session, "raw-a").id == "raw-a"


def test_parsed_document_uuid_resolves_to_its_raw_document(session):
    assert _resolve_raw_document(session, "doc-a").id == "raw-a"


def test_source_slash_doc_id(session):
    assert _resolve_raw_document(session, "mu2e-wiki/200-dup").id == "raw-dup-2"


def test_bare_unique_doc_id(session):
    assert _resolve_raw_document(session, "100-a").id == "raw-a"


def test_ambiguous_doc_id_fails_and_lists_candidates(session):
    with pytest.raises(ValueError) as e:
        _resolve_raw_document(session, "200-dup")
    assert "raw-dup-1" in str(e.value) and "raw-dup-2" in str(e.value)


def test_document_without_raw_document_fails(session):
    with pytest.raises(ValueError, match="no raw document"):
        _resolve_raw_document(session, "doc-orphan")


def test_unknown_identifier_fails(session):
    with pytest.raises(ValueError, match="no raw document matches"):
        _resolve_raw_document(session, "mu2e-docdb/nope")
