"""EMBED_MAX_TEXT_CHARS: a giant spreadsheet is represented by its summary chunk only.

The bulk parse once chunked 103 spreadsheets of millions of characters into
709k chunks, 32% of all embeddings (2026-11-15), because only the import
job passed a size limit. chunk_and_embed() now enforces it for every path.
"""
from types import SimpleNamespace

import pytest

from kb_mcp.kb.embedding import embedding as emb


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def fake_chunk_document(document, chunk_strategy=None, config=None, session=None):
        seen.append(chunk_strategy)
        return []

    import kb_mcp.kb.embedding.chunking as chunking
    monkeypatch.setattr(chunking, "chunk_document", fake_chunk_document)
    monkeypatch.setattr(emb, "get_db_session", lambda session=None: __import__("contextlib").nullcontext(SimpleNamespace(add=lambda *_: None, flush=lambda: None, commit=lambda: None)))
    return seen


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"


def doc(chars, doc_type="text", source_type=XLSX):
    return SimpleNamespace(id="d1", doc_type=doc_type, text="x" * chars, source_id="s", source_type=source_type)


def test_default_limit_is_500k(monkeypatch):
    from kb_mcp.config import get_embedding_config
    monkeypatch.delenv("EMBED_MAX_TEXT_CHARS", raising=False)
    assert get_embedding_config()["max_text_chars"] == 500_000


def test_a_giant_spreadsheet_is_never_chunked(monkeypatch, calls):
    monkeypatch.setenv("EMBED_MAX_TEXT_CHARS", "1000")
    assert emb.chunk_and_embed(doc(5_000), chunk_strategy="tokens") == []
    assert calls == [], "chunk_document must not run for an oversized document"


def test_its_summary_chunk_is_still_made(monkeypatch, calls):
    monkeypatch.setenv("EMBED_MAX_TEXT_CHARS", "1000")
    try:
        emb.chunk_and_embed(doc(5_000), chunk_strategy="summary")
    except Exception:  # noqa: BLE001 - the fake session cannot log; reaching the chunker is the point
        pass
    assert calls == ["summary"]


@pytest.mark.parametrize("chars, doc_type, source_type, limit", [
    (900, "text", XLSX, "1000"),      # a small spreadsheet
    (5_000, "image", XLSX, "1000"),   # a record, not text
    (5_000, "text", XLSX, "0"),       # no limit
    (5_000, "text", PDF, "1000"),     # a long PDF is always chunked: its text is the content
    (5_000, "text", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "1000"),
])
def test_everything_else_is_chunked(monkeypatch, calls, chars, doc_type, source_type, limit):
    monkeypatch.setenv("EMBED_MAX_TEXT_CHARS", limit)
    try:
        emb.chunk_and_embed(doc(chars, doc_type, source_type), chunk_strategy="tokens")
    except Exception:  # noqa: BLE001
        pass
    assert calls == ["tokens"]
