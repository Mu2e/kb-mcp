"""The document list must not load full document text it does not show.

get(defer_content=True) leaves Document.text/binary out of the query; the list
then serialises with document_to_dict(include_text=False). Reading doc.text
there would lazy-load each document's full text one row at a time -- the cause
of multi-second /api/get responses.
"""

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import defer

from kb_mcp.kb.db_models import Document, Source
from kb_mcp.server.web.routes.documents import document_to_dict


def _add_doc(db_session):
    db_session.add(Source(id="test-deferred", name="Deferred"))
    doc = Document(
        source_id="test-deferred",
        source_type="text/plain",
        doc_type="text",
        title="Big",
        text="x" * 5000,
    )
    db_session.add(doc)
    db_session.commit()
    doc_id = doc.id
    db_session.expunge_all()
    return doc_id


def test_deferred_text_stays_unloaded(db_session):
    doc_id = _add_doc(db_session)
    doc = (
        db_session.query(Document)
        .options(defer(Document.text), defer(Document.binary))
        .filter(Document.id == doc_id)
        .one()
    )

    result = document_to_dict(doc, include_text=False)

    assert "text" in sa_inspect(doc).unloaded, "document_to_dict loaded the deferred text"
    assert "text" not in result and "text_preview" not in result
    assert result["title"] == "Big"


def test_loaded_text_still_gives_a_preview(db_session):
    doc_id = _add_doc(db_session)
    doc = db_session.query(Document).filter(Document.id == doc_id).one()

    result = document_to_dict(doc, include_text=False)

    assert result["text_preview"] == "x" * 300
    assert result["text_length"] == 5000
