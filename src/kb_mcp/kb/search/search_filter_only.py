"""Filter-only listing: what kb_search does with an empty query.

An empty query means "no ranking, just the filter" -- e.g. every figure of one
DocDB entry, or a few images to look at. Hybrid search has nothing to work
with then: full-text matches nothing and the embedding of "" ranks noise. So
the documents the filter passes are listed directly, most recently added to
the knowledge base first (insert_time).

Two steps, because one plan does not fit both kinds of filter. The rows the
filter passes are fetched unordered first, up to CANDIDATE_CAP (0.2 s even for
a doc_id wildcard). Fewer than that: the filter is narrow, and those rows are
sorted exactly. More: the filter is broad, and the ordered query walks the
insert_time index backwards and stops after max_results rows -- fast because
matches are dense. The ordered query alone took 90 s for doc_id 32713-*: the
four matches are old, so the walk read most of the table first.

Why insert_time and not the document's own date: creating_time is set on
about 4% of the DocDB records (2026-11-18), so ordering on it would put the
other 96% first in no particular order. insert_time is always set and
indexed: for a broad filter the planner walks that index backwards and stops
after max_results rows (doc_type=image: 0.1 s).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, func, select

from ..database import get_db_session
from ..db_models import DOCUMENT_VISIBILITY_DEFAULT_GROUP, Document, DocumentVisibilityCurrent
from .filters import get_filters_fallback

ORDER = "most recently added first (empty query: filter only, not ranked)"
CANDIDATE_CAP = 2000


def search_filter_only(
    filter: Optional[Dict[str, Any]] = None,
    max_results: int = 5,
    source_id: Optional[str] = None,
    doc_type: Optional[str] = None,
    enforce_visibility: bool = False,
    session=None,
) -> Dict[str, Any]:
    """Documents passing ``filter``, newest insert_time first.

    Returns the standard search response shape (``results`` of
    ``{"document", "chunks"}``) with no chunks: a small document is shown in
    full by the formatter, a larger one as its summary or a preview.
    """
    started = time.time()
    with get_db_session(session) as session:
        dialect_name = session.bind.dialect.name if session.bind else None
        limit = max(1, int(max_results))

        def passing(*columns):
            stmt = select(*columns)
            conditions = get_filters_fallback(
                Document, source_id=source_id, doc_type=doc_type, filter=filter,
                dialect_name=dialect_name,
            )
            if conditions:
                stmt = stmt.where(and_(*conditions))
            if enforce_visibility:
                # Same rule as the other paths: no current-visibility row, or
                # visible_search left NULL, means visible.
                stmt = stmt.outerjoin(
                    DocumentVisibilityCurrent,
                    and_(DocumentVisibilityCurrent.raw_document_id == Document.raw_document_id,
                         DocumentVisibilityCurrent.group_name == DOCUMENT_VISIBILITY_DEFAULT_GROUP),
                ).where(func.coalesce(DocumentVisibilityCurrent.visible_search, True))
            return stmt

        candidates = session.execute(
            passing(Document.id, Document.insert_time).limit(CANDIDATE_CAP + 1)).all()
        if len(candidates) <= CANDIDATE_CAP:
            path = "narrow"
            newest = sorted(candidates, key=lambda c: (c.insert_time or datetime.min, c.id),
                            reverse=True)[:limit]
            ids = [c.id for c in newest]
            by_id = {d.id: d for d in session.execute(
                select(Document).where(Document.id.in_(ids))).scalars()} if ids else {}
            documents = [by_id[i] for i in ids if i in by_id]
        else:
            path = "broad"
            # insert_time alone: a tie-breaker column keeps the planner off
            # the index (6.8 s instead of 0.1 s for doc_type=image).
            documents = session.execute(
                passing(Document).order_by(Document.insert_time.desc()).limit(limit)).scalars().all()

    return {
        "results": [{"document": doc, "chunks": []} for doc in documents],
        "metadata": {"search_type": "filter_only", "order": ORDER, "path": path,
                     "elapsed_s": round(time.time() - started, 3)},
    }
