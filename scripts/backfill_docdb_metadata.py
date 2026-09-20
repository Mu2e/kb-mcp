#!/usr/bin/env python3
"""Refresh DocDB metadata on raw rows that were imported without timestamps.

The original DocDB import stored a title and the file bytes but no dates, so
those rows carry no `created` / `revised_content` and their parsed Documents
have a null `creating_time` / `update_time`. Search and the web UI order on
those columns, and the importer's staleness check reads them to decide whether
a document needs re-downloading, so the gap keeps costing us on every pass.

This only touches metadata: one DocDB metadata request per document number, no
file downloads and no re-parsing, so it is safe to run while a parse or import
is in flight.

Run:
    scripts/backfill_docdb_metadata.py --dry-run
    scripts/backfill_docdb_metadata.py --delay 1
"""
import argparse
import logging
import re
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional

from kb_mcp.imports.docdb import DocDBSource, parse_docdb_datetime
from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import Document, RawDocument

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

# Leading DocDB number, whichever doc_id convention the row was written under:
# bare "56548", legacy "56539/stem", current "56359-stem".
_DOC_NUMBER = re.compile(r"^(\d+)(?:[-/]|$)")

# Copied onto every row of a document; DocDB holds these per document, not
# per file, so all of a document's rows get the same values.
_META_FIELDS = (
    "title", "abstract", "authors", "topics", "created",
    "revised_content", "revised_meta", "version",
)


def doc_number(doc_id: Optional[str]) -> Optional[str]:
    match = _DOC_NUMBER.match(doc_id or "")
    return match.group(1) if match else None


def find_stale_rows(session, source_id: str, refresh_all: bool) -> Dict[str, List[RawDocument]]:
    """Group raw rows needing a metadata refresh by DocDB number."""
    rows = session.query(RawDocument).filter(RawDocument.source_id == source_id).all()
    grouped: Dict[str, List[RawDocument]] = defaultdict(list)
    unparseable = 0
    for row in rows:
        number = doc_number(row.doc_id)
        if number is None:
            unparseable += 1
            continue
        meta = row.meta or {}
        if refresh_all or not (meta.get("created") or meta.get("revised_content")):
            grouped[number].append(row)
    if unparseable:
        log.warning(f"{unparseable} row(s) have a doc_id with no leading number — skipped")
    return grouped


def find_meta_repairable(session, source_id: str) -> List[RawDocument]:
    """Rows whose stored metadata never reached their Documents.

    The dates are already in `meta` — these rows were ingested before
    `parse_docdb_datetime()` was wired into the importer, and `kb reparse`
    only carries forward whatever the Document already had, so a null stays
    null however many times it is re-parsed. Nothing here needs DocDB.
    """
    rows = (
        session.query(RawDocument)
        .join(Document, Document.raw_document_id == RawDocument.id)
        .filter(
            RawDocument.source_id == source_id,
            Document.creating_time.is_(None),
        )
        .distinct()
        .all()
    )
    return [
        row for row in rows
        if (row.meta or {}).get("created") or (row.meta or {}).get("revised_content")
    ]


def run_from_meta(source_id: str, dry_run: bool) -> int:
    """Propagate stored meta dates onto Documents, without contacting DocDB."""
    with get_db_session() as session:
        rows = find_meta_repairable(session, source_id)
        log.info(f"{len(rows)} raw row(s) have stored dates missing from their Documents")
        if not rows or dry_run:
            for row in rows[:20]:
                log.info(f"  would repair {row.doc_id}")
            if len(rows) > 20:
                log.info(f"  ... and {len(rows) - 20} more")
            return 0

        repaired = 0
        for index, row in enumerate(rows, 1):
            apply_metadata(session, [row], row.meta or {})
            repaired += 1
            if index % 50 == 0:
                session.commit()
                log.info(f"  {index}/{len(rows)} rows")
        session.commit()
        log.info(f"Done: {repaired} row(s) repaired from stored metadata")
    return 0


def apply_metadata(session, rows: List[RawDocument], meta: dict) -> int:
    """Write DocDB metadata onto the raw rows and their parsed Documents."""
    creating_time = parse_docdb_datetime(meta.get("created"))
    update_time = parse_docdb_datetime(meta.get("revised_content"))
    touched = 0

    for row in rows:
        merged = dict(row.meta or {})
        for field in _META_FIELDS:
            value = meta.get(field)
            if value not in (None, "", [], {}):
                merged[field] = value
        docdb_id = meta.get("docid") or meta.get("docdb_id")
        if docdb_id is not None:
            merged.setdefault("docdb_id", docdb_id)
        # JSONB is only re-serialised when the attribute is reassigned.
        row.meta = merged
        touched += 1

        for document in session.query(Document).filter(
            Document.raw_document_id == row.id
        ):
            if creating_time is not None:
                document.creating_time = creating_time
            if update_time is not None:
                document.update_time = update_time
    return touched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-id", default="mu2e-docdb")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between DocDB requests (default: 1.0)")
    parser.add_argument("--limit", type=int,
                        help="Only process the first N documents")
    parser.add_argument("--all", action="store_true", dest="refresh_all",
                        help="Refresh every row, not just those missing dates")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing")
    parser.add_argument("--from-meta", action="store_true",
                        help="Contact nothing: copy dates already stored in meta onto "
                             "Documents whose creating_time is null")
    args = parser.parse_args()

    if args.from_meta:
        return run_from_meta(args.source_id, args.dry_run)

    with get_db_session() as session:
        grouped = find_stale_rows(session, args.source_id, args.refresh_all)

    numbers = sorted(grouped, key=int)
    if args.limit:
        numbers = numbers[: args.limit]
    row_count = sum(len(grouped[n]) for n in numbers)
    log.info(f"{len(numbers)} document(s) / {row_count} raw row(s) need metadata")
    if not numbers:
        return 0

    if args.dry_run:
        for number in numbers[:20]:
            ids = ", ".join(r.doc_id or "?" for r in grouped[number][:3])
            log.info(f"  would refresh {number}: {ids}")
        if len(numbers) > 20:
            log.info(f"  ... and {len(numbers) - 20} more")
        return 0

    updated = inaccessible = failed = 0
    with DocDBSource(source_id=args.source_id, delay=args.delay) as source:
        for index, number in enumerate(numbers, 1):
            log.info(f"[{index}/{len(numbers)}] doc {number}")
            try:
                meta = source.get_meta(int(number))
            except Exception as exc:
                failed += 1
                log.warning(f"  doc {number}: metadata fetch failed: {exc}")
                continue

            if meta is None:
                inaccessible += 1
                log.warning(f"  doc {number}: not found or not accessible — row left as is")
                continue

            # Re-attach in a fresh session per document so one bad write cannot
            # roll back everything already repaired.
            with get_db_session() as session:
                rows = session.query(RawDocument).filter(
                    RawDocument.id.in_([r.id for r in grouped[number]])
                ).all()
                try:
                    touched = apply_metadata(session, rows, meta)
                    session.commit()
                    updated += touched
                    log.info(f"  doc {number}: updated {touched} row(s) "
                             f"(created={meta.get('created')!r})")
                except Exception as exc:
                    session.rollback()
                    failed += 1
                    log.error(f"  doc {number}: write failed: {exc}")

            if args.delay > 0:
                time.sleep(args.delay)

    log.info(f"Done: {updated} row(s) updated, {inaccessible} inaccessible, {failed} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
