"""Read-only consumers for the persisted structured parser output.

Lives outside `kb/parser/` because the parser writes
`documents.parser_output`; this module reads it back. No new LLM calls,
no schema changes — just helpers that turn the persisted DoclingDocument
JSON into structured views the search response builders,
hierarchical-context expander, and (eventually) the page-rendering
pipeline can consume.

`parser_output` is parser-agnostic — it holds whichever parser's raw
structured output produced the document — so every reader here guards on
`is_docling_document()` before walking the payload.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .database import get_db_session

# The guard lives with the schema in db_models (PR-1 territory) and is
# re-exported here because this module is the canonical read-side consumer.
from .db_models import Document, is_docling_document  # noqa: F401

logger = logging.getLogger(__name__)


def _materialise_neighbor(
    cref: str,
    docling_json: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Resolve a body `cref` into a compact neighbor dict.

    Returns None for unresolved or empty crefs (skipped by the caller).
    """
    if not cref or not isinstance(cref, str):
        return None

    if cref.startswith("#/texts/"):
        try:
            idx = int(cref.split("/")[-1])
        except ValueError:
            return None
        texts = docling_json.get("texts") or []
        if not (0 <= idx < len(texts)):
            return None
        t = texts[idx] or {}
        text = (t.get("text") or "").strip()
        if not text:
            return None
        prov = t.get("prov") or []
        page = prov[0].get("page_no") if prov and isinstance(prov[0], dict) else None
        return {
            "cref": cref,
            "kind": "text",
            "label": t.get("label"),
            "text": text,
            "page": page,
        }

    if cref.startswith("#/tables/"):
        try:
            idx = int(cref.split("/")[-1])
        except ValueError:
            return None
        tables = docling_json.get("tables") or []
        if not (0 <= idx < len(tables)):
            return None
        tab = tables[idx] or {}
        prov = tab.get("prov") or []
        page = prov[0].get("page_no") if prov and isinstance(prov[0], dict) else None
        # Caption text refs are nested under `captions`; we don't resolve
        # them here — callers that want the rendered caption can use the
        # table record's meta["caption"] which the parser already extracted.
        return {
            "cref": cref,
            "kind": "table",
            "page": page,
            "self_ref": tab.get("self_ref"),
        }

    if cref.startswith("#/pictures/"):
        try:
            idx = int(cref.split("/")[-1])
        except ValueError:
            return None
        pictures = docling_json.get("pictures") or []
        if not (0 <= idx < len(pictures)):
            return None
        pic = pictures[idx] or {}
        prov = pic.get("prov") or []
        page = prov[0].get("page_no") if prov and isinstance(prov[0], dict) else None
        return {
            "cref": cref,
            "kind": "picture",
            "page": page,
            "self_ref": pic.get("self_ref"),
        }

    if cref.startswith("#/groups/"):
        try:
            idx = int(cref.split("/")[-1])
        except ValueError:
            return None
        groups = docling_json.get("groups") or []
        if not (0 <= idx < len(groups)):
            return None
        g = groups[idx] or {}
        # Materialise list-style group bodies — useful for "what does the
        # bullet list right after Figure 3 say?" queries.
        items: List[str] = []
        texts = docling_json.get("texts") or []
        for sub in g.get("children") or []:
            sub_cref = sub.get("cref") if isinstance(sub, dict) else None
            if not sub_cref or not sub_cref.startswith("#/texts/"):
                continue
            try:
                sidx = int(sub_cref.split("/")[-1])
            except ValueError:
                continue
            if 0 <= sidx < len(texts):
                txt = (texts[sidx].get("text") or "").strip()
                if txt:
                    items.append(txt)
        if not items:
            return None
        return {
            "cref": cref,
            "kind": "group",
            "label": g.get("label"),
            "items": items,
        }

    # Unhandled cref kinds (form_items, key_value_items, etc.) — skip.
    return None


def get_neighbors(
    parent_doc_id: str,
    self_ref: str,
    window: int = 1,
    session=None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Return body-order neighbors of `self_ref` inside the parent's DoclingDocument.

    Args:
        parent_doc_id: UUID of the parent text Document (its
            `parser_output` must hold a DoclingDocument payload).
        self_ref: Body self-reference of the anchor element, e.g.
            `"#/tables/2"`, `"#/pictures/3"`, or `"#/texts/17"`.
        window: How many body children to return on each side.
        session: Optional database session.

    Returns:
        Dict with two keys:
            "before": list of up to `window` neighbor dicts in body order
                      (closest neighbour last).
            "after":  list of up to `window` neighbor dicts in body order
                      (closest neighbour first).

        Each neighbor dict has at least `{cref, kind}` plus kind-specific
        fields (`text`/`label`/`page` for texts, `page` for tables/pictures,
        `items` for groups).

    Returns empty lists if `parent_doc_id` doesn't exist, has no walkable
    DoclingDocument `parser_output`, or the `self_ref` is not in the body
    order.
    """
    empty = {"before": [], "after": []}
    if window <= 0 or not self_ref:
        return empty

    with get_db_session(session) as session:
        doc = session.query(Document).filter(Document.id == parent_doc_id).first()
        if doc is None:
            logger.debug(f"get_neighbors: parent_doc_id={parent_doc_id!r} not found")
            return empty
        docling_json = doc.parser_output
        if not is_docling_document(docling_json):
            logger.debug(
                f"get_neighbors: parent {parent_doc_id} has no DoclingDocument parser_output"
            )
            return empty

    body = (docling_json.get("body") or {})
    children = body.get("children") or []

    # Find anchor index. Body children are dicts like {"cref": "#/tables/2"}.
    anchor_idx = None
    for i, child in enumerate(children):
        if isinstance(child, dict) and child.get("cref") == self_ref:
            anchor_idx = i
            break
    if anchor_idx is None:
        return empty

    before_raw = children[max(0, anchor_idx - window):anchor_idx]
    after_raw = children[anchor_idx + 1: anchor_idx + 1 + window]

    before = [
        n for n in (
            _materialise_neighbor(c.get("cref") if isinstance(c, dict) else None, docling_json)
            for c in before_raw
        ) if n is not None
    ]
    after = [
        n for n in (
            _materialise_neighbor(c.get("cref") if isinstance(c, dict) else None, docling_json)
            for c in after_raw
        ) if n is not None
    ]

    return {"before": before, "after": after}
