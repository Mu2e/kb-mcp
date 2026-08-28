"""Unit tests for window-aware summary chunking.

`document.summary` used to be stored as a single chunk however long it was, so
everything past the embedding model's window was silently embedded as nothing
(84% of the corpus's summaries were over it). Summaries are now split against
the same `EmbedBudget` the Docling walker and the image/table record path use.

The split is an indexing device: search collapses the pieces back to one
result carrying the whole summary (see `kb.search.chunk_text`), so these tests
check the *chunking* invariants — full coverage, nothing over the cap — and the
collapse separately.

Uses a private on-disk SQLite engine and patches the module-level session
factory, since chunk_document() persists chunks.
"""

import re
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import kb_mcp.kb.database as kbdb
from kb_mcp.kb.db_models import Base, Document, Source
from kb_mcp.kb.embedding.budget import get_embed_budget
from kb_mcp.kb.embedding.chunking import chunk_document
from kb_mcp.kb.search.chunk_text import attach_chunk_text


@pytest.fixture()
def summary_session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path}/summaries.db")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(kbdb, "_engine", engine, raising=False)
    monkeypatch.setattr(kbdb, "_Session", factory, raising=False)
    session = factory()
    session.add(Source(id="test-summary", name="summary"))
    session.commit()
    yield session
    session.close()


def _make_doc(session, summary):
    doc = Document(
        id=str(uuid.uuid4()),
        source_id="test-summary",
        doc_id=f"summary-{uuid.uuid4().hex[:6]}",
        doc_type="text",
        source_type="text/plain",
        text="Body text of the document, not the summary.",
        summary=summary,
        meta={},
    )
    session.add(doc)
    session.commit()
    return doc


def _cap():
    return get_embed_budget(
        prepend_section_path=False, prepend_gist=False
    ).content_budget()


SHORT_SUMMARY = (
    "The Mu2e experiment searches for coherent muon-to-electron conversion "
    "in the field of a nucleus, probing charged lepton flavour violation."
)

LONG_SUMMARY = " ".join(
    f"Sentence {i} restates a distinct finding about the tracker, the "
    f"calorimeter and the cosmic ray veto in enough words to accumulate "
    f"a real token count." for i in range(120)
)

# Real summaries contain paragraph breaks, and the splitter drops the `\n\n`
# between pieces (a chunk should not open on a blank line). A fixture without
# them would not exercise that path.
LONG_PARAGRAPHED_SUMMARY = "\n\n".join(
    " ".join(
        f"Paragraph {p} sentence {i} sets out a separate result with enough "
        f"words behind it to register a real token count." for i in range(12)
    )
    for p in range(8)
)


def _words(text):
    """Whitespace-insensitive content, for comparing a split against its source."""
    return re.sub(r"\s+", " ", text).strip()


def test_short_summary_stays_one_chunk(summary_session):
    doc = _make_doc(summary_session, SHORT_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    assert len(chunks) == 1
    assert chunks[0].text == SHORT_SUMMARY
    assert chunks[0].meta.get("split_total") is None


def test_long_summary_splits_and_stays_under_the_cap(summary_session):
    doc = _make_doc(summary_session, LONG_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    cap = _cap()
    assert len(chunks) > 1, "a summary well past the window must split"
    assert all(c.token_length <= cap for c in chunks), (
        f"cap={cap}, got {[c.token_length for c in chunks]}"
    )


@pytest.mark.parametrize("summary", [LONG_SUMMARY, LONG_PARAGRAPHED_SUMMARY])
def test_split_summary_loses_no_content(summary_session, summary):
    """The whole point: every part of the summary reaches some embedding.

    Compared whitespace-insensitively — the splitter drops the `\\n\\n` between
    paragraphs, so the pieces do not concatenate back byte-for-byte.
    """
    doc = _make_doc(summary_session, summary)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    ordered = sorted(chunks, key=lambda c: c.chunk_index)
    assert len(ordered) > 1
    assert _words(" ".join(c.text for c in ordered)) == _words(summary)


def test_split_summary_is_contiguously_indexed(summary_session):
    """chunk_index must run 0..n-1 — it is the only ordering the row carries
    (chunk `meta` is not persisted, and summary chunks have no offsets)."""
    doc = _make_doc(summary_session, LONG_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    assert sorted(c.chunk_index for c in chunks) == list(range(len(chunks)))


def test_summary_chunks_carry_no_offsets(summary_session):
    """Offsets would index into `document.summary`, and the search layer's
    positioned branch would slice `document.text` with them."""
    doc = _make_doc(summary_session, LONG_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    assert all(c.char_start_index is None for c in chunks)
    assert all(c.char_end_index is None for c in chunks)


def test_summary_chunks_embed_verbatim(summary_session):
    """No Section:/Context: prefix — the budget was computed without one."""
    doc = _make_doc(summary_session, LONG_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    assert all(c.embed_text() == c.text for c in chunks)


class _Doc:
    def __init__(self, text=None, summary=None):
        self.text = text
        self.summary = summary


def test_search_collapses_split_summary_to_one_full_summary():
    doc = _Doc(text="body", summary="THE WHOLE SUMMARY")
    chunks = [
        {"chunk_strategy": "summary", "chunk_index": i, "similarity": s,
         "char_start": None, "char_end": None}
        for i, s in enumerate([0.4, 0.9, 0.6])
    ]
    out = attach_chunk_text(chunks, doc, score_key="similarity")
    assert len(out) == 1, "three pieces of one summary must yield one result"
    assert out[0]["text"] == "THE WHOLE SUMMARY"
    assert out[0]["similarity"] == 0.9, "the best-scoring piece is the one kept"


def test_search_keeps_positioned_chunks_alongside_a_summary():
    doc = _Doc(text="0123456789", summary="SUM")
    chunks = [
        {"chunk_strategy": "summary", "similarity": 0.5,
         "char_start": None, "char_end": None},
        {"chunk_strategy": "tokens_1000_200", "similarity": 0.8,
         "char_start": 2, "char_end": 6},
        {"chunk_strategy": "summary", "similarity": 0.1,
         "char_start": None, "char_end": None},
    ]
    out = attach_chunk_text(chunks, doc, score_key="similarity")
    assert [c["text"] for c in out] == ["2345", "SUM"]


def test_search_summary_fallback_when_document_has_no_summary():
    doc = _Doc(text="body", summary=None)
    chunks = [{"chunk_strategy": "summary", "similarity": 0.5,
               "char_start": None, "char_end": None}]
    out = attach_chunk_text(chunks, doc, score_key="similarity")
    assert len(out) == 1
    assert "text" not in out[0], "no summary to rebuild from; leave text unset"


def test_fusion_collapses_summary_pieces_from_different_backends():
    """RRF merges on chunk_id, so the vector and full-text backends can each
    contribute a *different* piece of the same summary. Both rebuild to the
    same text, so the fused document must still show one."""
    from kb_mcp.kb.search.chunk_text import collapse_summary_chunks

    fused = [
        {"chunk_id": "a", "chunk_strategy": "summary", "rrf_score": 0.03,
         "text": "THE WHOLE SUMMARY"},
        {"chunk_id": "b", "chunk_strategy": "tokens_1000_200", "rrf_score": 0.05,
         "text": "a body passage"},
        {"chunk_id": "c", "chunk_strategy": "summary", "rrf_score": 0.04,
         "text": "THE WHOLE SUMMARY"},
    ]
    out = collapse_summary_chunks(fused, score_key="rrf_score")
    assert [c["chunk_id"] for c in out] == ["b", "c"]


# --- window-tagged strategy name -------------------------------------------

def test_base_strategy_strips_only_the_window_suffix():
    from kb_mcp.chunking import base_strategy

    assert base_strategy("summary_256") == "summary"
    assert base_strategy("summary_512") == "summary"
    # Legacy rows keep matching every summary predicate.
    assert base_strategy("summary") == "summary"
    # Must NOT be a generic "strip trailing _<digits>" rule.
    assert base_strategy("tokens_1000_200") == "tokens_1000_200"
    assert base_strategy("image") == "image"
    assert base_strategy("") == ""


def test_summary_chunks_are_stored_under_the_window_tagged_name(summary_session):
    doc = _make_doc(summary_session, LONG_SUMMARY)
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    window = get_embed_budget().window
    assert all(c.chunk_strategy == f"summary_{window}" for c in chunks)


def test_rechunk_supersedes_a_legacy_untagged_summary_row(summary_session):
    """Writing summary_256 while a plain `summary` row survived would leave the
    document with two summary chunk sets, the old one still truncated."""
    from kb_mcp.kb.embedding.db_models import Chunk, ChunkStrategy

    doc = _make_doc(summary_session, LONG_SUMMARY)
    summary_session.add(ChunkStrategy(strategy="summary", meta={}))
    summary_session.add(Chunk(
        document_id=doc.id, chunk_index=0, chunk_strategy="summary",
        text=LONG_SUMMARY, token_length=9999,
    ))
    summary_session.commit()

    chunk_document(doc, chunk_strategy="summary", session=summary_session)

    remaining = summary_session.query(Chunk).filter(
        Chunk.document_id == doc.id, Chunk.chunk_strategy == "summary"
    ).all()
    assert remaining == [], "the legacy untagged row must be replaced, not kept"


def test_split_summary_still_embeds_verbatim_under_the_tagged_name(summary_session):
    """base_strategy() must reach embed_text(), or a tagged summary picks up a
    Section:/Context: prefix it was never budgeted for."""
    doc = _make_doc(summary_session, LONG_SUMMARY)
    doc.gist = "a gist that would otherwise be prepended"
    summary_session.commit()
    chunks = chunk_document(doc, chunk_strategy="summary", session=summary_session)
    assert all(c.embed_text() == c.text for c in chunks)
