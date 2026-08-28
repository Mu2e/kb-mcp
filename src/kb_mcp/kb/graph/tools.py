"""Batch processing tools for knowledge graph extraction."""

import logging
from typing import Optional, Dict, Any, List

from tqdm import tqdm

from ..database import get_db_session
from ..db_models import Document
from .db_models import GraphExtractionLog
from .extraction import extract_and_process_document

logger = logging.getLogger(__name__)


def extract_all(
    source_id: Optional[str] = None,
    parser_id: Optional[str] = None,
    doc_types: Optional[List[str]] = None,
    force: bool = False,
    limit: Optional[int] = None,
    batch_size: Optional[int] = None,
    session=None,
) -> Dict[str, Any]:
    """
    Extract knowledge graph relations from all documents matching filters.

    This function processes documents in batch, similar to parse_all and chunk_and_embed_all.
    By default, it skips documents that already have extraction logs (unless force=True),
    and only processes documents with `doc_type="text"` — tables and images
    largely re-cover the parent text and would otherwise produce duplicate relations.

    Args:
        source_id: Optional source ID filter.
        parser_id: Optional parser ID filter.
        doc_types: List of doc_type strings to include. Defaults to ["text"] — graph
                   relations are extracted from the parent text doc only. Pass
                   ["text", "table"] (or similar) to opt structural records back in.
        force: If True, re-process documents even if they already have extraction logs.
               If False (default), skip documents with existing extraction logs.
        limit: If provided, process only this many documents (useful for testing/incremental processing).
        batch_size: If provided, process in batches with LOCK/SKIP LOCKED for parallel processing.
                   If None, uses default from get_batch_config()['extract_batch_size'].
                   Set to a large value (e.g., 999999) to disable batching and process all at once.
        session: Optional database session.

    Returns:
        Statistics dict:
        {
            "total_documents": int,          # Total documents matching filters
            "processed": int,                # Documents successfully processed
            "skipped": int,                  # Documents skipped (if force=False)
            "errors": int,                   # Documents that failed
            "total_relations_extracted": int,
            "total_relations_created": int,
            "total_relations_updated": int,
            "total_relations_errors": int,
            "error_details": List[Dict[str, Any]]
        }
    """
    if doc_types is None:
        doc_types = ["text"]
    from ...config import get_batch_config

    # Get batch size from config if not provided
    batch_size = batch_size or get_batch_config()['extract_batch_size']

    processed = 0
    errors = 0
    total_relations_extracted = 0
    total_relations_created = 0
    total_relations_updated = 0
    total_relations_errors = 0
    error_details = []
    total_processed = 0

    # Process in batches with lock/commit cycle
    while True:
        with get_db_session() as session:
            # Build query to find documents that need extraction
            # If not force, use NOT EXISTS to find documents without extraction logs
            if not force:
                subquery = session.query(GraphExtractionLog.document_id).filter(
                    GraphExtractionLog.document_id == Document.id
                ).exists()

                query = session.query(Document).filter(~subquery)
            else:
                # If force, process all documents
                query = session.query(Document)

            # Apply filters
            if source_id:
                query = query.filter(Document.source_id == source_id)
            if parser_id:
                query = query.filter(Document.parser_id == parser_id)
            if doc_types:
                query = query.filter(Document.doc_type.in_(doc_types))

            # Apply limit if provided (overall limit across all batches)
            if limit and total_processed >= limit:
                break

            # Calculate how many to fetch in this batch
            batch_limit = batch_size
            if limit:
                batch_limit = min(batch_size, limit - total_processed)

            # For parallel processing: lock a batch of rows
            # SKIP LOCKED allows other workers to grab different rows
            documents = query.limit(batch_limit).with_for_update(skip_locked=True).all()

            if not documents:
                # No more documents to process
                break

            if total_processed == 0:
                logger.info(
                    f"Processing documents in batches of {batch_size} "
                    f"(source_id={source_id}, parser_id={parser_id}, doc_types={doc_types}, force={force}, limit={limit})"
                )

            for document in tqdm(documents, desc="Extracting graph relations", unit="doc", disable=total_processed > 0):
                try:
                    result = extract_and_process_document(
                        document_id=document.id,
                        session=session
                    )

                    processed += 1
                    total_relations_extracted += result["relations_extracted"]
                    total_relations_created += result["relations_created"]
                    total_relations_updated += result["relations_updated"]
                    total_relations_errors += result["relations_errors"]

                    if result.get("error_details"):
                        error_details.extend(result["error_details"])

                    logger.debug(
                        f"Processed document {document.id}: "
                        f"{result['relations_extracted']} extracted, "
                        f"{result['relations_created']} created, "
                        f"{result['relations_updated']} updated, "
                        f"{result['relations_errors']} errors"
                    )

                except Exception as e:
                    errors += 1
                    error_msg = {
                        "document_id": document.id,
                        "error": str(e),
                        "stage": "document_processing"
                    }
                    error_details.append(error_msg)
                    logger.error(f"Failed to process document {document.id}: {e}", exc_info=True)
                    continue

            total_processed += len(documents)
            # Commit happens here when session context exits, releasing locks

    if total_processed == 0:
        logger.info(
            f"No documents found to extract "
            f"(source_id={source_id}, parser_id={parser_id}, doc_types={doc_types}, force={force})"
        )

    logger.info(
        f"Batch extraction complete: {processed}/{total_processed} documents processed, "
        f"{total_relations_created} relations created, "
        f"{total_relations_updated} relations updated, "
        f"{errors} document errors, "
        f"{total_relations_errors} relation errors"
    )

    return {
        "total_documents": total_processed,
        "processed": processed,
        "skipped": 0,  # Skipping happens at query level now
        "errors": errors,
        "total_relations_extracted": total_relations_extracted,
        "total_relations_created": total_relations_created,
        "total_relations_updated": total_relations_updated,
        "total_relations_errors": total_relations_errors,
        "error_details": error_details
    }
