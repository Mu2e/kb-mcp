"""Re-chunk documents whose existing chunks exceed the new 1500-token cap.

Picks up Documents whose largest chunk is over 1500 tokens (was the
pre-Task-45/46 "single chunk per record" or "single oversized span"
behaviour) and re-runs chunk_document() on them so they benefit from
the new sub-chunking logic. Targets doc_type in {section, table, text}.

Run:
    .venv/bin/python -u scripts/rechunk_oversized.py
"""
import logging
import os
import sys
import time

# Force docling_json strategy on text records — required for the Task 4 step 3
# path to fire and re-chunk the 2 long text docs whose docling_json chunks
# exceeded the (old) cap.
os.environ.setdefault("CHUNK_FROM_DOCLING_JSON", "true")

from sqlalchemy import text

from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import Document
from kb_mcp.kb.embedding.chunking import chunk_document

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def main():
    with get_db_session() as s:
        rows = s.execute(text("""
            SELECT DISTINCT d.id::text AS doc_id, d.doc_type
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            WHERE c.token_length > 1500
                  AND d.doc_type IN ('section', 'table', 'text')
            ORDER BY doc_id
        """)).all()
    total = len(rows)
    log.info(f"Found {total} docs needing re-chunk (token_length > 1500)")

    t0 = time.time()
    processed = 0
    failed = 0
    skipped = 0

    batch_size = 25
    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        with get_db_session() as s:
            for doc_id, doc_type in batch:
                try:
                    doc = s.query(Document).filter(Document.id == doc_id).first()
                    if doc is None:
                        skipped += 1
                        continue

                    # Delete existing chunks + embeds (same pattern as the
                    # VLM redo: cascade-delete embeddings_st_minilml6v2 first
                    # via FK, then chunks).
                    s.execute(
                        text("""
                            DELETE FROM embeddings_st_minilml6v2 WHERE chunk_id IN (
                                SELECT id FROM chunks WHERE document_id = :doc_id
                            )
                        """),
                        {"doc_id": doc_id},
                    )
                    s.execute(
                        text("DELETE FROM chunks WHERE document_id = :doc_id"),
                        {"doc_id": doc_id},
                    )
                    s.flush()

                    # Re-chunk + re-embed using the new pipeline
                    new_chunks = doc.chunk_and_embed()
                    processed += 1

                    if processed % 10 == 0:
                        elapsed = time.time() - t0
                        rate = processed / elapsed if elapsed > 0 else 0
                        eta_sec = (total - processed) / rate if rate > 0 else 0
                        log.info(
                            f"  progress {processed}/{total} ({100*processed/total:.0f}%), "
                            f"rate {rate:.2f}/sec, ETA {eta_sec/60:.0f} min, "
                            f"failed={failed} skipped={skipped}"
                        )
                except Exception as e:
                    failed += 1
                    log.warning(f"  failed for {doc_id[:8]} ({doc_type}): {type(e).__name__}: {str(e)[:200]}")
                    s.rollback()
            s.commit()

    elapsed = time.time() - t0
    log.info(f"DONE: {processed} re-chunked, {failed} failed, {skipped} skipped in {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
