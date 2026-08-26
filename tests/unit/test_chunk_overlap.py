"""Unit tests for the token chunker's size/overlap defaults.

`chunk_overlap` used to default to a flat 200 tokens against a 1000-token
chunk. The chunker strides `chunk_size - chunk_overlap`, so an absolute
default degrades as the size changes: pair a leftover 200 with a
window-sized 254 and the stride is 54 (~4.7x index inflation); at
`overlap >= chunk_size` the stride is zero and the loop never terminates.

The default is now a fraction of the chunk size, resolved to a concrete
integer so the strategy name stays literal, and clamped to half the size.
"""

import os

import pytest

from kb_mcp.chunking.chunking import (
    DEFAULT_CHUNK_OVERLAP_FRACTION,
    DEFAULT_CHUNK_SIZE,
    TokensStrategy,
    get_strategy_name,
)


def _resolved(**cfg):
    r = TokensStrategy._ensure_defaults(cfg)
    return r["chunk_size"], r["chunk_overlap"]


def test_overlap_defaults_to_a_fraction_of_chunk_size():
    size, overlap = _resolved()
    assert size == DEFAULT_CHUNK_SIZE
    assert overlap == round(DEFAULT_CHUNK_SIZE * DEFAULT_CHUNK_OVERLAP_FRACTION)


@pytest.mark.parametrize("chunk_size", [100, 254, 512, 1000, 4000])
def test_overlap_scales_with_chunk_size(chunk_size):
    """The point of a fraction: the same relative overlap at any size."""
    size, overlap = _resolved(chunk_size=chunk_size)
    assert overlap == round(size * DEFAULT_CHUNK_OVERLAP_FRACTION)


@pytest.mark.parametrize(
    "chunk_size,chunk_overlap",
    [(254, 300), (100, 100), (1000, 1000), (512, 5000), (10, 11)],
)
def test_oversized_overlap_is_clamped_so_the_stride_advances(chunk_size, chunk_overlap):
    """Without the clamp these configs stride by <=0 and never terminate."""
    size, overlap = _resolved(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    stride = size - overlap
    assert stride >= 1, "the chunker would loop forever"
    assert stride >= size / 2, "a token must not land in three or more chunks"


def test_an_explicit_reasonable_overlap_is_left_alone():
    """Callers passing the old 1000/200 keep exactly that, name included."""
    assert _resolved(chunk_size=1000, chunk_overlap=200) == (1000, 200)
    assert get_strategy_name("tokens", {"chunk_size": 1000, "chunk_overlap": 200}) == (
        "tokens_1000_200"
    )


def test_zero_overlap_is_honoured_not_treated_as_unset():
    assert _resolved(chunk_size=1000, chunk_overlap=0) == (1000, 0)


def test_strategy_name_carries_the_resolved_integers():
    """Resolved, not left as a fraction — the name has to be a literal so it
    can be matched against `chunks.chunk_strategy`."""
    assert get_strategy_name("tokens", {}) == "tokens_1000_100"
    assert get_strategy_name("tokens", {"chunk_size": 512}) == "tokens_512_51"


def test_chunking_a_long_text_respects_the_resolved_size(monkeypatch):
    from kb_mcp.chunking import chunk

    text = " ".join(f"word{i}" for i in range(4000))
    chunks = chunk(text, strategy="tokens", config={"chunk_size": 200})
    assert len(chunks) > 1
    assert all(c["token_length"] <= 200 for c in chunks)
    assert all(c["chunk_strategy"] == "tokens_200_20" for c in chunks)


# --- env plumbing ----------------------------------------------------------

@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv("CHUNK_SIZE", raising=False)
    monkeypatch.delenv("CHUNK_OVERLAP", raising=False)


def test_env_defaults_when_unset(clean_env):
    from kb_mcp.config import get_embedding_config

    cfg = get_embedding_config()
    # None, not a coded default: the embedding layer has to be able to tell
    # "the operator chose 1000" from "nobody said", and size to the model's
    # window in the second case.
    assert cfg["chunk_size"] is None
    assert cfg["chunk_overlap"] is None, "unset must mean 'derive from size'"


def test_env_overrides(clean_env, monkeypatch):
    from kb_mcp.config import get_embedding_config

    monkeypatch.setenv("CHUNK_SIZE", "512")
    monkeypatch.setenv("CHUNK_OVERLAP", "77")
    cfg = get_embedding_config()
    assert (cfg["chunk_size"], cfg["chunk_overlap"]) == (512, 77)


def test_empty_chunk_overlap_is_unset_not_zero(clean_env, monkeypatch):
    """`FOO=` in .env means unset in this codebase, not an explicit value —
    an empty CHUNK_OVERLAP must not silently disable overlap entirely."""
    from kb_mcp.config import get_embedding_config

    monkeypatch.setenv("CHUNK_OVERLAP", "")
    assert get_embedding_config()["chunk_overlap"] is None


def test_resolve_strategy_name_sees_the_env(clean_env, monkeypatch):
    """chunk_and_embed_all predicts the stored name with this; if it ignored
    CHUNK_SIZE it would look for chunks the chunker never wrote."""
    from kb_mcp.kb.embedding.chunking import resolve_strategy_name

    monkeypatch.setenv("CHUNK_SIZE", "512")
    assert resolve_strategy_name("tokens") == "tokens_512_51"


# --- embedding-budget enforcement ------------------------------------------
#
# The token chunker slices by tiktoken offsets; the encoder reads word-pieces
# and truncates silently. No constant converts between them — measured over
# 150 real documents, the worst chunk came to 7.9x its tiktoken count, not the
# 1.32x normal prose shows. So sizing is a target and measurement is the
# guarantee.

class _Doc:
    def __init__(self, text, gist=None):
        self.id = "deadbeef-0000"
        self.text = text
        self.gist = gist


def _budget(**kw):
    from kb_mcp.kb.embedding.budget import get_embed_budget
    return get_embed_budget(**kw)


def test_enforce_is_a_noop_for_chunks_already_within_budget():
    from kb_mcp.kb.embedding.chunking import enforce_embed_budget

    text = "Short enough to fit comfortably inside any window."
    cds = [{"text": text, "chunk_index": 0, "char_start_index": 0,
            "char_end_index": len(text), "token_length": 9,
            "chunk_strategy": "tokens_137_14", "meta": {}}]
    out = enforce_embed_budget(list(cds), _Doc(text))
    assert len(out) == 1
    assert out[0]["text"] == text


def test_dense_content_is_resplit_until_it_actually_fits():
    """A markdown table tokenizes far denser in word-pieces than in tiktoken —
    exactly the case a ratio-derived chunk_size gets wrong."""
    from kb_mcp.kb.embedding.chunking import enforce_embed_budget

    dense = "\n".join(
        r"| a | 1.23e-4 | 5.6789 | x_{i}^{2} | \alpha\beta\gamma |"
        for _ in range(300)
    )
    doc = _Doc(dense)
    b = _budget()
    cds = [{"text": dense, "chunk_index": 0, "char_start_index": 0,
            "char_end_index": len(dense), "token_length": 0,
            "chunk_strategy": "tokens_137_14", "meta": {}}]
    assert b.count(dense) > b.content_budget(), "fixture must actually overflow"

    out = enforce_embed_budget(cds, doc)
    assert len(out) > 1
    cap = b.content_budget()
    for c in out:
        assert b.count(c["text"]) <= cap, "a piece still exceeds the window"


def test_resplit_pieces_stay_exact_slices_and_are_reindexed():
    from kb_mcp.kb.embedding.chunking import enforce_embed_budget

    dense = "\n".join(r"| col | 1.2345e-6 | \gamma_{ij} |" for _ in range(400))
    doc = _Doc(dense)
    out = enforce_embed_budget(
        [{"text": dense, "chunk_index": 0, "char_start_index": 0,
          "char_end_index": len(dense), "token_length": 0,
          "chunk_strategy": "tokens_137_14", "meta": {}}],
        doc,
    )
    assert [c["chunk_index"] for c in out] == list(range(len(out)))
    for c in out:
        assert dense[c["char_start_index"]:c["char_end_index"]] == c["text"]


def test_gist_is_charged_against_the_budget():
    """embed_text() prepends `Context: {gist}`, so a long gist must shrink the
    room left for the body — otherwise the prefix pushes the chunk over."""
    from kb_mcp.kb.embedding.chunking import enforce_embed_budget

    # ~72 word-pieces: the longest gist measured across 500 corpus documents.
    gist = " ".join(f"gist word {i}" for i in range(18))
    body = " ".join(f"body sentence number {i} here." for i in range(200))
    doc = _Doc(body, gist=gist)
    b = _budget(gist=gist)

    out = enforce_embed_budget(
        [{"text": body, "chunk_index": 0, "char_start_index": 0,
          "char_end_index": len(body), "token_length": 0,
          "chunk_strategy": "tokens_137_14", "meta": {}}],
        doc,
    )
    for c in out:
        embedded = f"Context: {gist}\n\n{c['text']}"
        assert b.count(embedded) + 2 <= b.window, "prefix + body must fit"


def test_token_chunk_size_is_derived_from_the_window_not_hardcoded():
    from kb_mcp.kb.embedding.budget import token_chunk_size

    small, large = token_chunk_size(window=256), token_chunk_size(window=512)
    assert large > small, "a bigger window must allow bigger chunks"
    assert small < 256, "must leave room for specials and the gist prefix"
