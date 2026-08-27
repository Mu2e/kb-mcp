"""Regression tests for parse_all() absorbing `kb reparse`'s modes.

`parse_all()` used to only ever look at RawDocuments with no Document yet —
`--force-reparse` was accepted but never changed that query, so it was a
dead flag. `kb reparse`/`--from-raw`/`--from-stored` had the real logic
(identity resolution by (source_id, doc_id) rather than raw_document_id,
timestamp restoration, stale-chunk cleanup) but ran as a plain sequential
loop with no row locking, so it couldn't be run in parallel.

These tests pin the merged behavior directly against parse_all()/
_parse_all_from_stored(), using an in-memory SQLite database (FOR UPDATE/
SKIP LOCKED/`of=` are silently no-ops there, so these tests exercise
row-selection and identity-resolution logic, not the Postgres-specific
locking itself).
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb import database, tools
from kb_mcp.kb.db_models import Base, Document, DocumentParserOutput, Parser, RawDocument, Source
from kb_mcp.kb.embedding.db_models import Chunk


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Point kb_mcp.kb.database at a throwaway SQLite file for this test."""
    url = f"sqlite:///{tmp_path}/parse_all_test.db"
    monkeypatch.setattr(database, "get_database_url", lambda: url)
    database._engine = None
    database._SessionLocal = None
    Base.metadata.create_all(database.get_engine())
    session = sessionmaker(bind=database.get_engine())()
    session.add(Source(id="src", name="src"))
    session.add(Parser(name="kb-mcp"))
    session.commit()
    yield session
    session.close()
    database._engine = None
    database._SessionLocal = None


def _raw(source_id="src", doc_id="doc-1", file_path=None, uri=None):
    return RawDocument(
        id=str(uuid.uuid4()),
        source_id=source_id,
        doc_id=doc_id,
        source_type="application/pdf",
        content_hash=uuid.uuid4().hex,
        file_path=file_path,
        uri=uri,
    )


def _doc(source_id="src", doc_id="doc-1", parser_id="kb-mcp", raw_document_id=None,
         parent_id=None, text="body", creating_time=None, update_time=None):
    return Document(
        id=str(uuid.uuid4()),
        source_id=source_id,
        doc_id=doc_id,
        source_type="application/pdf",
        doc_type="text",
        parser_id=parser_id,
        raw_document_id=raw_document_id,
        parent_id=parent_id,
        text=text,
        content_hash="h" * 64,
        creating_time=creating_time,
        update_time=update_time,
    )


class _FakeAddDocument:
    def __init__(self):
        self.calls = []

    def __call__(self, file_path, **kwargs):
        self.calls.append({"file_path": file_path, **kwargs})
        return {"document_ids": ["new-doc-id"], "num_documents": 1}


class _FakeIngest:
    def __init__(self):
        self.calls = []

    def __call__(self, file_path, **kwargs):
        self.calls.append({"file_path": file_path, **kwargs})
        return {"document_ids": [kwargs.get("session") and "reparsed-doc-id" or "reparsed-doc-id"]}


@pytest.fixture()
def fake_add_document(monkeypatch):
    fake = _FakeAddDocument()
    monkeypatch.setattr("kb_mcp.kb.documents.add_document", fake)
    return fake


@pytest.fixture()
def fake_ingest(monkeypatch):
    fake = _FakeIngest()
    monkeypatch.setattr(tools, "ingest", fake)
    return fake


def _touch(tmp_path, name="doc.pdf"):
    p = tmp_path / name
    p.write_text("x")
    return str(p)


class TestForceReparseRowSelection:
    def test_default_skips_raw_with_existing_document(self, db, tmp_path, fake_add_document, fake_ingest):
        fp = _touch(tmp_path)
        raw = _raw(file_path=fp)
        db.add(raw)
        db.add(_doc(raw_document_id=raw.id))
        db.commit()

        result = tools.parse_all(source_id="src")

        assert fake_add_document.calls == []
        assert fake_ingest.calls == []
        assert result["total_raw"] == 0

    def test_force_reparse_reprocesses_existing(self, db, tmp_path, fake_add_document, fake_ingest):
        fp = _touch(tmp_path)
        raw = _raw(file_path=fp)
        db.add(raw)
        db.add(_doc(raw_document_id=raw.id))
        db.commit()

        result = tools.parse_all(source_id="src", force_reparse=True)

        assert fake_add_document.calls == []
        assert len(fake_ingest.calls) == 1
        assert result["parsed"] == 1


class TestIdentityResolution:
    def test_resolves_by_source_and_doc_id_not_raw_document_id(
        self, db, tmp_path, fake_add_document, fake_ingest
    ):
        """A second, re-fetched raw row for the same (source_id, doc_id) must
        still find the existing Document by business key, not by raw_document_id
        (which points at the *first* raw row)."""
        # Naive datetimes: SQLite's DateTime(timezone=True) round-trips the
        # value but drops the tzinfo annotation, which would make an
        # equality assert here about that SQLite quirk instead of identity
        # resolution.
        created = datetime(2026, 1, 1)
        updated = datetime(2026, 2, 1)

        fp1 = _touch(tmp_path, "v1.pdf")
        fp2 = _touch(tmp_path, "v2.pdf")
        raw1 = _raw(doc_id="doc-1", file_path=fp1)
        raw2 = _raw(doc_id="doc-1", file_path=fp2)
        db.add_all([raw1, raw2])
        existing = _doc(doc_id="doc-1", raw_document_id=raw1.id,
                         creating_time=created, update_time=updated)
        db.add(existing)
        chunk = Chunk(id=str(uuid.uuid4()), document_id=existing.id, text="c", chunk_index=0)
        db.add(chunk)
        db.commit()
        existing_id = existing.id
        chunk_id = chunk.id

        # Only raw2 is eligible under the default (non-force) query since raw1's
        # (source_id, doc_id, parser) already has a Document — force_reparse
        # widens both, but we only seeded one Document either way.
        result = tools.parse_all(source_id="src", force_reparse=True)

        assert len(fake_ingest.calls) == 2  # both raw rows resolve to the same existing doc
        for call in fake_ingest.calls:
            assert call["creating_time"] == created
            assert call["update_time"] == updated
            assert call["doc_id"] == "doc-1"

        # The stale chunk tied to the pre-existing document was queued for
        # deletion once the (mocked) reparse "succeeded".
        assert db.query(Chunk).filter(Chunk.id == chunk_id).count() == 0
        assert result["parsed"] == 2


class TestFromStored:
    def test_reaches_document_with_no_raw_file(self, db, monkeypatch):
        doc = _doc(raw_document_id=None, text="stale text")
        db.add(doc)
        db.add(DocumentParserOutput(
            document_id=doc.id,
            output={"schema_name": "DoclingDocument", "version": "1.0.0"},
        ))
        db.commit()
        doc_id = doc.id

        monkeypatch.setattr(tools, "_rebuild_text_from_parser_output", lambda d, s: "rebuilt text")

        result = tools.parse_all(source_id="src", from_stored=True, generate_summary=False, chunk_and_embed=False)

        db.expire_all()
        refreshed = db.query(Document).filter(Document.id == doc_id).first()
        assert refreshed.text == "rebuilt text"
        assert result["parsed"] == 1


class TestDryRun:
    def test_dry_run_makes_no_calls_or_changes(self, db, tmp_path, fake_add_document, fake_ingest):
        fp = _touch(tmp_path)
        db.add(_raw(file_path=fp))
        db.commit()

        result = tools.parse_all(source_id="src", dry_run=True)

        assert fake_add_document.calls == []
        assert fake_ingest.calls == []
        assert result["dry_run"] is True
        assert result["total"] == 1
        assert result["targets"][0]["doc_id"] == "doc-1"


class TestEmptyOnlyGuard:
    def test_raises_without_force_reparse_or_from_stored(self, db):
        with pytest.raises(ValueError):
            tools.parse_all(source_id="src", empty_only=True)

    def test_excludes_non_empty_existing_documents(self, db, tmp_path, fake_ingest):
        fp = _touch(tmp_path)
        raw = _raw(file_path=fp)
        db.add(raw)
        db.add(_doc(raw_document_id=raw.id, text="not empty"))
        db.commit()

        result = tools.parse_all(source_id="src", force_reparse=True, empty_only=True)

        assert fake_ingest.calls == []
        assert result["skipped"] == 1
