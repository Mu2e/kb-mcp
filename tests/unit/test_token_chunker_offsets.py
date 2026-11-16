"""The token chunker's character offsets come from one pass, not a decode per chunk.

Decoding tokens[:start] for every chunk was quadratic; documents with millions
of tokens (slide decks with embedded tables) took hours (2026-11-16).
"""
import time

import tiktoken

from kb_mcp.chunking.chunking import TokensStrategy

ENC = tiktoken.get_encoding("cl100k_base")
TEXT = ("Die Größe der Kalorimeterkristalle | 0.0234 | 1.5e-03 | straw tube 😀 alignment. " * 400)


def _old_char_starts(text, size, overlap):
    tokens = ENC.encode(text)
    starts, s = [], 0
    while s < len(tokens):
        e = min(s + size, len(tokens))
        starts.append(0 if s == 0 else len(ENC.decode(tokens[:s])))
        s = e - overlap
        if e >= len(tokens):
            break
    return starts


def test_offsets_match_the_prefix_decoding():
    chunks = TokensStrategy.chunk(TEXT, {"chunk_size": 64, "chunk_overlap": 8}, ENC)
    assert [c["char_start_index"] for c in chunks] == _old_char_starts(TEXT, 64, 8)
    for c in chunks:
        assert c["char_end_index"] == c["char_start_index"] + len(c["text"])


def test_a_long_text_is_chunked_in_linear_time():
    text = TEXT * 60            # ~1.5M tokens' worth of the old cost
    t0 = time.time()
    chunks = TokensStrategy.chunk(text, {"chunk_size": 256, "chunk_overlap": 25}, ENC)
    assert len(chunks) > 1000
    assert time.time() - t0 < 5           # 22 s with the prefix decoding


def test_an_encoding_without_offsets_still_works():
    class Plain:
        encode = staticmethod(ENC.encode)
        decode = staticmethod(ENC.decode)
    chunks = TokensStrategy.chunk(TEXT, {"chunk_size": 64, "chunk_overlap": 8}, Plain())
    assert [c["char_start_index"] for c in chunks] == _old_char_starts(TEXT, 64, 8)
