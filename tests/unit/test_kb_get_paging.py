"""Unit tests for kb_get paging.

A few documents run to hundreds of millions of characters, so kb_get must
never return a whole document in one call: it pages, and each partial page
tells the model the offset of the next one.
"""

from types import SimpleNamespace

import pytest

import kb_mcp.config as config_mod
import kb_mcp.kb as kb_mod
from kb_mcp.server import mcp as mcp_mod
from kb_mcp.server.mcp import KB_GET_MAX_CHARS_LIMIT, kb_get

TEXT = "".join(chr(ord("a") + i % 26) for i in range(250))


@pytest.fixture(autouse=True)
def fake_doc(monkeypatch):
    doc = SimpleNamespace(
        title="Big table", title_gen=None, doc_id="123", source_id="mu2e-docdb",
        doc_type="table", uri=None, meta=None, parser_id="docling", text=TEXT,
    )
    monkeypatch.setattr(kb_mod, "get", lambda identifier: doc)
    monkeypatch.setattr(
        config_mod, "get_server_config",
        lambda: {"kb_get_max_chars": 100, "hide_graph": False},
    )
    nodes = [{"name": "Tracker", "type": "detector", "mention_count": 3, "id": "n1"}]
    monkeypatch.setattr(mcp_mod, "get_nodes_for_document", lambda doc_id: nodes)
    return doc


def _content(out):
    return out.split("[[CONTENT_START]]\n", 1)[1].rsplit("\n", 1)[0]


def test_first_page_uses_default_size_and_points_to_next():
    out = kb_get("mu2e-docdb/123")
    assert _content(out) == TEXT[:100]
    assert "Length: 250 characters" in out
    assert "Showing: characters 0-100 of 250" in out
    assert "[[DETECTED_CONCEPTS]]" in out
    assert out.endswith(
        '[[DOCUMENT_CONTINUES: showing characters 0-100 of 250. '
        'Call kb_get(identifier="mu2e-docdb/123", offset=100) for the next part.]]'
    )
    assert "[[DOCUMENT_END]]" not in out


def test_middle_page_skips_concepts():
    out = kb_get("mu2e-docdb/123", offset=100)
    assert _content(out) == TEXT[100:200]
    assert "[[DETECTED_CONCEPTS]]" not in out
    assert "offset=200" in out


def test_last_page_ends_the_document():
    out = kb_get("mu2e-docdb/123", offset=200)
    assert _content(out) == TEXT[200:]
    assert "Showing: characters 200-250 of 250" in out
    assert out.endswith("[[DOCUMENT_END]]")
    assert "DOCUMENT_CONTINUES" not in out


def test_short_document_is_returned_whole():
    out = kb_get("mu2e-docdb/123", max_chars=1000)
    assert _content(out) == TEXT
    assert "Showing:" not in out
    assert out.endswith("[[DOCUMENT_END]]")


def test_offset_past_end_is_an_error_not_an_empty_page():
    out = kb_get("mu2e-docdb/123", offset=250)
    assert out.startswith("ERROR: offset 250 is past the end")


def test_invalid_arguments_are_errors():
    assert kb_get("mu2e-docdb/123", offset=-1).startswith("ERROR: offset")
    assert kb_get("mu2e-docdb/123", max_chars=0).startswith("ERROR: max_chars")


def test_max_chars_is_clamped(fake_doc):
    fake_doc.text = "x" * (KB_GET_MAX_CHARS_LIMIT + 50)
    out = kb_get("mu2e-docdb/123", max_chars=10 * KB_GET_MAX_CHARS_LIMIT)
    assert len(_content(out)) == KB_GET_MAX_CHARS_LIMIT
    assert f"offset={KB_GET_MAX_CHARS_LIMIT})" in out


def test_empty_document_still_answers(fake_doc):
    fake_doc.text = None
    out = kb_get("mu2e-docdb/123")
    assert "No content available" in out
    assert out.endswith("[[DOCUMENT_END]]")
