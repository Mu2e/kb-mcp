"""Hierarchical context expansion for search hits.

When `expand_context=True` is passed to a search call, this module attaches
parent provenance + reading-order neighbours to every section / table /
image hit. Result enrichment only — no LLM calls, no schema change.

Dedup against hit text keeps the LLM's prompt context tight: if the
parent's summary or a neighbour paragraph is already a substring of the
hit's chunk text, it's dropped before being attached.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..database import get_db_session
from ..db_models import Document
from ..parser_views import get_neighbors

logger = logging.getLogger(__name__)


def _is_redundant(candidate: str, hit_text: str) -> bool:
    """Substring dedup. Cheap first-pass check.

    Returns True if `candidate` (a parent summary / gist / neighbour
    paragraph) is already present in `hit_text` and shouldn't be
    re-surfaced. Whitespace-normalised so trivial reflow doesn't escape
    the check.
    """
    if not candidate:
        return True
    cn = " ".join(candidate.split())
    if not cn:
        return True
    hn = " ".join(hit_text.split())
    return cn in hn


def attach_parent_provenance(
    final_results: List[Dict[str, Any]],
    session=None,
    window: int = 1,
) -> None:
    """Mutate `final_results` in place, attaching `parent_provenance` to every
    section / table / image hit.

    Each enriched result gains:
        result["parent_provenance"] = {
            "parent_doc_id":     parent text-doc's external doc_id,
            "parent_doc_uid":    parent text-doc's UUID,
            "parent_title":      str (if set),
            "parent_uri":        str (if set),
            "parent_summary":    str (if set AND not already in hit text),
            "parent_gist":       str (if set AND not already in hit text),
            "surrounding_text":  list[str] (neighbour text spans, deduped),
        }

    Text hits are skipped — they already are the parent.
    """
    if not final_results:
        return

    with get_db_session(session) as session:
        # Collect parent UUIDs in one pass to avoid N+1 queries.
        parent_uids = {
            r["document"].parent_id
            for r in final_results
            if r.get("document") is not None
            and r["document"].doc_type in ("section", "table", "image")
            and r["document"].parent_id
        }
        parents_by_uid: Dict[str, Document] = {}
        if parent_uids:
            for p in session.query(Document).filter(Document.id.in_(parent_uids)).all():
                parents_by_uid[p.id] = p

        for r in final_results:
            doc = r.get("document")
            if doc is None or doc.doc_type not in ("section", "table", "image"):
                continue
            parent = parents_by_uid.get(doc.parent_id) if doc.parent_id else None
            if parent is None:
                continue

            # Hit text = concat of all surfaced chunk texts (usually 1).
            hit_text_parts = [c.get("text") or "" for c in r.get("chunks", [])]
            hit_text = "\n\n".join(p for p in hit_text_parts if p)

            prov: Dict[str, Any] = {
                "parent_doc_id": parent.doc_id,
                "parent_doc_uid": parent.id,
            }
            title = parent.title or parent.title_gen
            if title:
                prov["parent_title"] = title
            if parent.uri:
                prov["parent_uri"] = parent.uri

            for key, attr in (("parent_summary", "summary"), ("parent_gist", "gist")):
                val = getattr(parent, attr, None)
                if val and not _is_redundant(val, hit_text):
                    prov[key] = val

            self_ref = (doc.meta or {}).get("self_ref")
            if self_ref:
                neighbours = get_neighbors(parent.id, self_ref, window=window, session=session)
                snippets: List[str] = []
                for n in neighbours.get("before", []) + neighbours.get("after", []):
                    if n.get("kind") != "text":
                        continue
                    txt = (n.get("text") or "").strip()
                    if not txt:
                        continue
                    if _is_redundant(txt, hit_text):
                        continue
                    # Avoid surfacing the same neighbour twice if it
                    # already overlaps an earlier snippet.
                    if any(_is_redundant(txt, prev) for prev in snippets):
                        continue
                    snippets.append(txt)
                if snippets:
                    prov["surrounding_text"] = snippets

            r["parent_provenance"] = prov
