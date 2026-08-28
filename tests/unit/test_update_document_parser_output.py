"""Re-parsing an existing document must carry its structured parser output over.

`_update_document()` copies the freshly parsed Document's fields onto the
existing row. It used to copy everything *except* `parser_output_ref`, so a
re-parse silently discarded the new DoclingDocument payload — leaving
`document_parser_outputs` empty and quietly downgrading chunking from the
section walker to plain token windows (chunking.py routes on
`is_docling_document(parser_output)`).
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, DocumentParserOutput, Source
from kb_mcp.kb.documents.operations import _update_document

DOCLING_PAYLOAD = {"schema_name": "DoclingDocument", "version": "1.0.0", "texts": []}


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/update_doc.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Source(id="test-src", name="test"))
    s.commit()
    yield s
    s.close()


def _doc(parser_output=None, text="body"):
    doc = Document(
        id=str(uuid.uuid4()),
        source_id="test-src",
        doc_id="doc-1",
        source_type="pdf",
        doc_type="text",
        text=text,
        content_hash="h" * 64,
    )
    if parser_output is not None:
        doc.parser_output_ref = DocumentParserOutput(output=parser_output)
    return doc


def test_reparse_attaches_new_parser_output(session):
    """Existing row had none (e.g. an earlier failed parse) — it gains one."""
    existing = _doc(text="")
    session.add(existing)
    session.commit()
    assert existing.parser_output is None

    _update_document(existing, _doc(parser_output=DOCLING_PAYLOAD), session, commit=False)

    assert existing.parser_output == DOCLING_PAYLOAD


def test_reparse_replaces_existing_parser_output(session):
    """A second re-parse overwrites the stale payload rather than keeping it."""
    existing = _doc(parser_output={"schema_name": "DoclingDocument", "version": "0.9.0"})
    session.add(existing)
    session.commit()

    _update_document(existing, _doc(parser_output=DOCLING_PAYLOAD), session, commit=False)

    assert existing.parser_output == DOCLING_PAYLOAD
    # One row per document, not an accumulating pile.
    assert session.query(DocumentParserOutput).count() == 1


def test_update_without_parser_output_keeps_existing(session):
    """A parser that emits no structured output must not wipe a good payload."""
    existing = _doc(parser_output=DOCLING_PAYLOAD)
    session.add(existing)
    session.commit()

    _update_document(existing, _doc(text="reparsed"), session, commit=False)

    assert existing.text == "reparsed"
    assert existing.parser_output == DOCLING_PAYLOAD
