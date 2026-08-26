"""Unit tests for Chunk.embed_text() — the contextual-embedding split.

`chunk.text` stores clean display text; the Section/Context prefix is built
dynamically at embed time. Special strategies and legacy prefixed chunks
pass through unchanged.
"""

from kb_mcp.kb.embedding.db_models import Chunk


def _chunk(**kwargs):
    defaults = dict(document_id="doc-1", chunk_index=0, chunk_strategy="tokens")
    defaults.update(kwargs)
    return Chunk(**defaults)


def test_prefixes_section_path():
    chunk = _chunk(text="Clean body text.", section_path="Intro > Scope")
    embedded = chunk.embed_text()
    assert embedded.startswith("Section: Intro > Scope")
    assert embedded.endswith("Clean body text.")
    # Display text stays clean.
    assert chunk.text == "Clean body text."


def test_no_section_path_returns_clean_text():
    chunk = _chunk(text="Body only.")
    assert chunk.embed_text() == "Body only."


def test_special_strategies_pass_through():
    for strategy in ("summary", "image", "table"):
        chunk = _chunk(
            text="Self-contained record text.",
            section_path="Should > Be > Ignored",
            chunk_strategy=strategy,
        )
        assert chunk.embed_text() == "Self-contained record text.", strategy


def test_section_strategy_gets_section_path_prefix():
    # "section"-strategy chunks are real section-boundary chunks of a text
    # document, not a self-contained record — they get the normal prefix.
    chunk = _chunk(
        text="Section body text.",
        section_path="Detector > Calorimeter",
        chunk_strategy="section",
    )
    embedded = chunk.embed_text()
    assert embedded.startswith("Section: Detector > Calorimeter")
    assert embedded.endswith("Section body text.")
    assert chunk.text == "Section body text."


def test_legacy_baked_prefix_not_double_prefixed():
    legacy = "Section: Old Path\n\nLegacy chunk body."
    chunk = _chunk(text=legacy, section_path="New Path")
    assert chunk.embed_text() == legacy


def test_legacy_context_prefix_not_double_prefixed():
    legacy = "Context: some old gist\n\nLegacy body."
    chunk = _chunk(text=legacy, section_path="New Path")
    assert chunk.embed_text() == legacy


def test_empty_text_is_safe():
    chunk = _chunk(text=None, chunk_strategy="summary")
    assert chunk.embed_text() == ""
