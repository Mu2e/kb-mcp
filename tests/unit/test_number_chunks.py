"""Long documents keep their prose chunks and leave out the number blocks.

Tables of numbers pasted into slides and Word files made the Mech
Integration decks 13.9k chunks each, almost all of them rows of numbers
(2026-11-16). drop_number_chunks leaves those out of a document longer than
EMBED_MAX_TEXT_CHARS; the summary chunk and normal-length documents are
never affected.
"""
from types import SimpleNamespace

import pytest

from kb_mcp.kb.embedding.chunking import drop_number_chunks, prose_words_per_kchar

PROSE = "The tracker straws were tested for leaks before installation in the detector hall. " * 6
NUMBERS = "| 0.0234 | 1.5e-03 | 2021-06-07T05:24:05 | 71.049 | | -3196.0 | " * 8
RULE = "|" + "-" * 200 + "|"


def _doc(chars, doc_type="text"):
    return SimpleNamespace(id="d1234567", doc_type=doc_type, text="x" * chars)


def _chunks(*texts, strategy="tokens_512"):
    return [{"text": t, "chunk_index": i, "chunk_strategy": strategy} for i, t in enumerate(texts)]


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setenv("EMBED_MAX_TEXT_CHARS", "1000")
    monkeypatch.delenv("EMBED_MIN_PROSE_WORDS_PER_KCHAR", raising=False)


def test_a_long_document_keeps_its_prose_and_drops_number_blocks():
    chunks = _chunks(PROSE, NUMBERS, RULE, PROSE, NUMBERS)
    kept = drop_number_chunks(_doc(5_000), chunks)
    assert [c["chunk_index"] for c in kept] == [0, 3]


def test_the_summary_chunk_always_stays():
    chunks = _chunks(NUMBERS, strategy="summary_256") + _chunks(PROSE, NUMBERS)
    kept = drop_number_chunks(_doc(5_000), chunks)
    assert [c["chunk_strategy"] for c in kept] == ["summary_256", "tokens_512"]
    assert drop_number_chunks(_doc(5_000), _chunks(NUMBERS, strategy="summary_256")) == _chunks(NUMBERS, strategy="summary_256")


def test_a_document_of_normal_length_keeps_everything():
    chunks = _chunks(PROSE, NUMBERS, RULE)
    assert drop_number_chunks(_doc(900), chunks) == chunks


def test_table_records_are_filtered_too_but_not_images():
    chunks = _chunks(PROSE, NUMBERS)
    assert len(drop_number_chunks(_doc(5_000, "table"), chunks)) == 1
    assert drop_number_chunks(_doc(5_000, "image"), chunks) == chunks


def test_a_document_of_only_numbers_keeps_one_body_chunk():
    """Left without a body chunk it would look unchunked and be chunked again on every run."""
    chunks = _chunks(NUMBERS, RULE, NUMBERS + " detector")
    kept = drop_number_chunks(_doc(5_000), chunks)
    assert [c["chunk_index"] for c in kept] == [2]


def test_the_threshold_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("EMBED_MIN_PROSE_WORDS_PER_KCHAR", "0")
    chunks = _chunks(PROSE, NUMBERS)
    assert drop_number_chunks(_doc(5_000), chunks) == chunks


def test_prose_density():
    assert prose_words_per_kchar(PROSE) > 100
    assert prose_words_per_kchar(NUMBERS) < 1
    assert prose_words_per_kchar(RULE) == 0
    assert prose_words_per_kchar("") == 0.0
    assert prose_words_per_kchar("Größe der Kalorimeterkristalle " * 20) > 50   # not ASCII-only
