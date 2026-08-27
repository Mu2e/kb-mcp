"""Data-consistency checks against stored documents.

A check is a small function over one document's text, registered in
`CHECKS`. `run_checks()` selects documents by doc_type/doc_id/source_id,
loads only the columns checks actually need (never `binary` — some rows
carry multi-MB blobs), and runs every requested check against each row.
Adding a new check is one function + one registry entry; no new query or
CLI plumbing.

Checks here started from two real bugs found by eye while browsing a
document: Docling's `<!-- image -->` and `<!-- formula-not-decoded -->`
markers surviving into `documents.text` because the substitution that was
supposed to replace them (`inline_docling_image_descriptions`,
`_fill_undecoded_formulas`) silently no-opped for that document. Both are
the same shape of bug — "a real value exists somewhere in the DB but a
leftover placeholder sits in the text instead" — so this module exists to
make that shape of bug something you scan for, not something you trip
over.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, NamedTuple, Optional

from .database import get_db_session
from .db_models import Document


class DocRef(NamedTuple):
    """The columns a check needs — never `binary`, which can be multi-MB."""
    id: str
    source_id: str
    doc_id: Optional[str]
    doc_type: str
    text: Optional[str]


@dataclass
class Issue:
    document_id: str
    source_id: str
    doc_id: Optional[str]
    doc_type: str
    check: str
    detail: str


def _check_image_markers(doc: DocRef) -> Optional[str]:
    """Docling's `<!-- image -->` placeholder left unsubstituted.

    Caused by `inline_docling_image_descriptions()` only scanning
    body-level children before 2026-08-27 — PPTX/DOCX nest pictures under
    per-slide/section groups, so the scan found none of them even though
    the descriptions existed on the child image records. Fixed going
    forward; existing rows need a `kb reparse --from-stored` to clear.
    """
    if not doc.text:
        return None
    n = doc.text.count("<!-- image -->")
    return f"{n} unsubstituted <!-- image --> marker(s)" if n else None


def _check_formula_markers(doc: DocRef) -> Optional[str]:
    """Docling's `<!-- formula-not-decoded -->` placeholder left unsubstituted.

    Caused by `_rebuild_text_from_parser_output()` (the `--from-stored`
    rebuild path) never calling `_fill_undecoded_formulas()` before
    2026-08-27, unlike the original parse path. Fixed going forward;
    existing rows need a `kb reparse --from-stored` to clear.
    """
    if not doc.text:
        return None
    n = doc.text.count("<!-- formula-not-decoded -->")
    return f"{n} unsubstituted <!-- formula-not-decoded --> marker(s)" if n else None


def _check_empty_text(doc: DocRef) -> Optional[str]:
    """A text document with no text at all — never chunkable."""
    if doc.doc_type == "text" and not (doc.text or "").strip():
        return "empty text"
    return None


CHECKS: Dict[str, Callable[[DocRef], Optional[str]]] = {
    "image-markers": _check_image_markers,
    "formula-markers": _check_formula_markers,
    "empty-text": _check_empty_text,
}


def run_checks(
    *,
    checks: Optional[List[str]] = None,
    doc_type: Optional[str] = None,
    doc_id: Optional[str] = None,
    source_id: Optional[str] = None,
    document_id: Optional[str] = None,
    session=None,
) -> List[Issue]:
    """Run the selected checks (default: all) over the selected documents.

    Filters are ANDed together and all optional — no filters means every
    document in the knowledge base.
    """
    names = checks or list(CHECKS)
    unknown = [n for n in names if n not in CHECKS]
    if unknown:
        raise ValueError(
            f"unknown check(s): {', '.join(unknown)} "
            f"(available: {', '.join(sorted(CHECKS))})"
        )

    with get_db_session(session) as session:
        query = session.query(
            Document.id, Document.source_id, Document.doc_id,
            Document.doc_type, Document.text,
        )
        if document_id:
            query = query.filter(Document.id == document_id)
        if doc_type:
            query = query.filter(Document.doc_type == doc_type)
        if doc_id:
            query = query.filter(Document.doc_id == doc_id)
        if source_id:
            query = query.filter(Document.source_id == source_id)

        issues: List[Issue] = []
        for row in query.yield_per(200):
            doc = DocRef(*row)
            for name in names:
                detail = CHECKS[name](doc)
                if detail:
                    issues.append(Issue(
                        document_id=doc.id, source_id=doc.source_id,
                        doc_id=doc.doc_id, doc_type=doc.doc_type,
                        check=name, detail=detail,
                    ))
    return issues
