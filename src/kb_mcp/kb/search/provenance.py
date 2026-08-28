"""Helpers that surface per-record provenance (page, caption, level, …)
into search-response dicts.

These pull straight from `Document.doc_type` and `Document.meta` — no schema
change, no new LLM call.
"""

from typing import Any, Dict


def doc_provenance(doc) -> Dict[str, Any]:
    """Return the provenance fields a search result should surface for `doc`.

    The output keys are intentionally optional — only included when the
    underlying meta key was populated by the parser. Callers spread this
    dict into their result objects so legacy clients don't see extra
    `null`-valued keys.

    Always includes:
        doc_type — so clients can branch on figure / table / text.

    Optionally includes (when the parser populated them):
        page          — int. From meta["page"] for table / image records.
        page_start    — int. From meta["page_start"], if present.
        page_end      — int. From meta["page_end"], if present.
        caption       — str. From meta["caption"]    for figure / table records.
        section_title — str. From meta["section_title"], if present.
        level         — int. From meta["level"], if present.
        num_rows      — int. From meta["num_rows"]   for table records.
        num_cols      — int. From meta["num_cols"]   for table records.

    Note: section-level info (section_path, page range, heading level) now
    lives on Chunk rows (chunk_strategy="section"), not a dedicated section
    Document — this function only ever saw the old section-doctype Document's
    meta, so these keys are unlikely to appear on a Document going forward.
    Kept generic (doesn't hardcode doc_type) since nothing here depends on it.
    """
    meta = (doc.meta or {}) if doc is not None else {}
    out: Dict[str, Any] = {"doc_type": getattr(doc, "doc_type", None)}

    page = meta.get("page")
    if page is not None:
        out["page"] = page

    for key in ("page_start", "page_end", "section_title", "level", "num_rows", "num_cols"):
        val = meta.get(key)
        if val is not None:
            out[key] = val

    caption = meta.get("caption")
    if caption:
        out["caption"] = caption

    return out
