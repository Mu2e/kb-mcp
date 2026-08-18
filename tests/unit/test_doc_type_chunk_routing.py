"""Unit tests for doc_type-aware chunk routing: image/section/table documents
must emit exactly one self-contained chunk regardless of the requested
strategy; text documents keep the requested strategy.

Uses a private on-disk SQLite engine and patches the module-level session
factory, since chunk_document() persists chunks.
"""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import kb_mcp.kb.database as kbdb
from kb_mcp.kb.db_models import Base, Document, Source
from kb_mcp.kb.embedding.chunking import chunk_document


@pytest.fixture()
def routed_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/routing.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    # chunk_document() may open sessions internally via get_db_session().
    monkeypatch.setattr(kbdb, "_engine", engine, raising=False)
    monkeypatch.setattr(kbdb, "_Session", factory, raising=False)
    session = factory()
    session.add(Source(id="test-routing", name="routing"))
    session.commit()
    yield session
    session.close()


def _make_doc(session, doc_type, text):
    doc = Document(
        id=str(uuid.uuid4()),
        source_id="test-routing",
        doc_id=f"routing-{doc_type}-{uuid.uuid4().hex[:6]}",
        doc_type=doc_type,
        source_type="text/plain",
        text=text,
        meta={},
    )
    session.add(doc)
    session.commit()
    return doc


LONG_TEXT = "\n\n".join(
    f"Paragraph {i} with enough repeated words to accumulate a real token "
    f"count across the document body for chunking purposes." for i in range(120)
)

SHORT_TEXT = (
    "Figure 3: calorimeter disk layout. The figure shows two annular disks "
    "of CsI crystals with SiPM readout on the downstream faces."
)


@pytest.mark.parametrize("special", ["image", "section", "table"])
def test_special_doc_types_emit_single_chunk(routed_session, special):
    doc = _make_doc(routed_session, special, SHORT_TEXT)
    chunks = chunk_document(doc, chunk_strategy="tokens", session=routed_session)
    assert len(chunks) == 1, f"{special} must emit exactly one chunk"
    assert chunks[0].chunk_strategy == special
    # Self-contained: embed_text must not add a contextual prefix.
    assert chunks[0].embed_text() == chunks[0].text


@pytest.mark.parametrize("special", ["section", "table"])
def test_oversized_special_records_subchunk_with_same_strategy(routed_session, special):
    """Records past the embed cap sub-chunk internally but keep the special
    strategy label so doc_type boosts still route to them."""
    doc = _make_doc(routed_session, special, LONG_TEXT)
    chunks = chunk_document(doc, chunk_strategy="tokens", session=routed_session)
    assert len(chunks) >= 2
    assert all(c.chunk_strategy == special for c in chunks)


def test_text_doc_type_keeps_token_chunking(routed_session):
    doc = _make_doc(routed_session, "text", LONG_TEXT)
    chunks = chunk_document(doc, chunk_strategy="tokens", session=routed_session)
    assert len(chunks) >= 2, "long text doc should token-chunk into several"
    assert all(c.chunk_strategy.startswith("tokens") for c in chunks)
