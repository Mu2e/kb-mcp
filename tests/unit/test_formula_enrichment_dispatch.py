"""The formula-enrichment auto path reads an attribute that must exist.

`parser_docling` decided whether to run CodeFormulaV2 by testing
`self.mime_type == "application/pdf"`. ParserBase stores that value as
`doc_type`, so the read raised AttributeError straight into the surrounding
`except Exception`, which silently left enrichment off — PARSE_FORMULA_
ENRICHMENT_AUTO never fired for any document, and because the `elif` sits
after the raising `if`, the manual flag was masked too.

Nothing failed loudly: documents just kept their `<!-- formula-not-decoded -->`
placeholders. These tests pin the attribute contract.
"""

import inspect
from pathlib import Path

import pytest

from kb_mcp.parser import parser_docling
from kb_mcp.parser.parser_docling import DoclingParser


def test_parser_exposes_the_attribute_the_dispatch_reads(tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    p = DoclingParser(str(pdf), "application/pdf")

    # The name the dispatch branches on.
    assert p.doc_type == "application/pdf"
    assert isinstance(p.file_path, Path)


def test_dispatch_does_not_read_a_nonexistent_mime_type_attribute():
    """Guards the exact typo: a unit test can't reach the inline dispatch
    without docling and a real PDF, so assert on the source instead."""
    src = inspect.getsource(parser_docling)
    assert "self.mime_type" not in src, (
        "parser_docling reads self.mime_type, which ParserBase does not set — "
        "the AttributeError is swallowed and silently disables enrichment"
    )


def test_dispatch_failures_are_not_logged_at_debug():
    """A swallowed failure disables a feature the operator switched on, so it
    must be visible at default log level."""
    src = inspect.getsource(parser_docling)
    start = src.index("formula-enrichment dispatch")
    line_start = src.rindex("logger.", 0, start)
    assert src[line_start:start].startswith("logger.warning"), (
        "formula-enrichment dispatch failure must log at warning, not debug"
    )
