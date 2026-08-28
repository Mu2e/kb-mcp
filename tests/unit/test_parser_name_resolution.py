"""`kb-mcp` is a request to pick a backend, not a backend.

Left unresolved it gets recorded as the thing that produced a document, so
every row claims "kb-mcp" whether Docling, Marker or the legacy text path ran.
`resolve_parser_name()` turns the sentinel into the backend that will actually
run, and `add_document()` applies it before the parser row is created.
"""

import pytest

from kb_mcp.parser.parse import AUTO_PARSER_NAMES, resolve_parser_name

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.parametrize("mime", ["application/pdf", PPTX, DOCX, "text/html",
                                  "application/xhtml+xml"])
@pytest.mark.parametrize("requested", AUTO_PARSER_NAMES)
def test_auto_resolves_to_docling_for_docling_defaults(mime, requested):
    assert resolve_parser_name(mime, requested) == "docling"


@pytest.mark.parametrize("requested", ["marker", "pypdf2", "nougat", "azure",
                                       "legacy", "marker-preloaded"])
def test_explicit_backend_is_never_overridden(requested):
    """Asking for a specific parser must survive resolution untouched."""
    assert resolve_parser_name("application/pdf", requested) == requested


@pytest.mark.parametrize("mime", ["text/markdown", "text/plain", None,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"])
def test_non_docling_types_pass_through(mime):
    """Types Docling doesn't default to keep whatever was requested; get_parser()
    resolves them further down."""
    assert resolve_parser_name(mime, "kb-mcp") == "kb-mcp"


def test_resolution_is_idempotent():
    """Safe to apply twice — add_document() resolves, then parse() resolves again."""
    once = resolve_parser_name("application/pdf", "kb-mcp")
    assert resolve_parser_name("application/pdf", once) == once == "docling"
