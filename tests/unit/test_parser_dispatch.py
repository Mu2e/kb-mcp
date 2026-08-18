"""Unit tests for parse() dispatch: default flips, explicit opt-outs, and
clean error paths for parser/MIME mismatches. No heavy parser is invoked —
only the routing layer and the plain-text path run.
"""

import pytest

from kb_mcp.parser import parse
from kb_mcp.parser.utils import PARSER_MAP


def _data(filename, binary=b"hello parser dispatch", **meta):
    return {
        "source_id": "test-dispatch",
        "doc_id": f"dispatch-{filename}",
        "binary": binary,
        "meta": {"filename": filename, **meta},
    }


def test_plaintext_routes_through_text_parser():
    results = parse(data=_data("note.txt"))
    assert len(results) == 1
    assert results[0]["source_type"] == "text/plain"
    assert "hello parser dispatch" in results[0]["text"]


def test_docling_rejects_unsupported_mime():
    with pytest.raises(NotImplementedError, match="[Dd]ocling"):
        parse(data=_data("note.txt"), parser_name="docling")


def test_pypdf2_rejects_non_pdf():
    with pytest.raises(NotImplementedError):
        parse(data=_data("note.txt"), parser_name="pypdf2")


def test_marker_rejects_non_pdf():
    with pytest.raises(NotImplementedError):
        parse(data=_data("note.txt"), parser_name="marker")


@pytest.mark.parametrize("optional", ["nougat", "azure"])
def test_optional_pdf_parsers_reject_non_pdf(optional):
    # MIME check fires before the lazy import, so this works without the
    # optional extras installed.
    with pytest.raises(NotImplementedError):
        parse(data=_data("note.txt"), parser_name=optional)


def test_legacy_optout_falls_through_to_bespoke_parser():
    results = parse(data=_data("note.txt"), parser_name="legacy")
    assert len(results) == 1
    assert "hello parser dispatch" in results[0]["text"]


def test_parser_map_covers_extension_keys():
    """The DocDB importer checks `file_ext not in PARSER_MAP` after stripping
    the dot — the map must keep bare-extension keys alongside MIME keys."""
    assert any("/" in k for k in PARSER_MAP), "expected MIME keys in PARSER_MAP"
    ext_keys = {k for k in PARSER_MAP if "/" not in k}
    assert {"pdf", "txt", "pptx", "docx", "xlsx"} <= ext_keys


def test_unknown_binary_extension_fails_cleanly():
    with pytest.raises(Exception):
        parse(data=_data("blob.xyz123", binary=b"\x00\x01\x02"))
