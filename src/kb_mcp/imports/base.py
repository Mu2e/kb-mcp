"""Base class for document importers."""

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Any

from ..config import get_data_dir

logger = logging.getLogger(__name__)


class Source(ABC):
    """Base class for document importers that fetch and process documents.
    
    Subclasses should implement:
    - `fetch_items()`: Fetch raw items from the source (e.g., API responses, file listings)
    - `process_item()`: Process a single item (download, parse, add to KB)
    
    The base class provides:
    - Multi-threaded processing with configurable workers
    - Progress tracking and logging
    - Error handling and retry logic
    - Database session management
    """
    
    def __init__(
        self,
        source_id: str,
        name: str,
        description: str = "",
        base_uri: str = "",
        delay: float = 0.5,
        timeout: float = 30.0,
        meta: Optional[Dict[str, Any]] = None,
    ):
        """Initialize the source.
        
        Args:
            source_id: Unique identifier for this source
            name: Human-readable name for the source
            description: Description of the source
            base_uri: Base URI for the source
            delay: Delay between requests in seconds (to be polite)
            timeout: Request timeout in seconds
            meta: Additional metadata to store with the source
        """
        self.source_id = source_id
        self.name = name
        self.description = description
        self.base_uri = base_uri
        self.delay = delay
        self.timeout = timeout
        self.meta = meta or {}
        self.meta.setdefault("source_class", self.__class__.__name__)
    
    @abstractmethod
    def fetch_items(self, query: Optional[str] = None, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch raw items from the source.
        
        Args:
            query: Optional query string to filter items
            max_results: Optional maximum number of items to fetch
            
        Returns:
            List of item dictionaries. Each item should have at least an 'id' field.
        """
        pass
    
    @abstractmethod
    def process_item(
        self,
        item: Dict[str, Any],
        output_dir: Path,
        session: Any,
    ) -> Dict[str, Any]:
        """Process a single item and add it to the knowledge base.

        Args:
            item: Item dictionary from fetch_items()
            output_dir: Directory to save downloaded files
            session: Database session for adding documents

        Returns:
            Dictionary with processing results. Required fields:
            - document_ids (list[str]): List of document IDs created (empty if failed)
            - num_documents (int): Number of documents created
            - parsed (bool): Whether parsing occurred
            - error (str|None): Error message if processing failed, None if successful
        """
        pass
    
    def process_all(
        self,
        query: Optional[str] = None,
        max_results: Optional[int] = None,
        output_dir: Optional[Path] = None,
        auto_embed: bool = True,
        auto_summarize: bool = True,
        max_embed_text_chars: Optional[int] = None,
        embed_images: bool = True,
        embed_tables: bool = True,
    ) -> List[Any]:
        """Fetch and process all items from the source.

        Args:
            query: Optional query string to filter items
            max_results: Optional maximum number of items to process
            output_dir: Directory to save downloaded files (default: data/sources/{source_id})
            auto_embed: If True, automatically chunk and embed all documents for this source
                       that don't have chunks yet (default: True)
            auto_summarize: If True, automatically generate summaries for all documents for this source
                           that don't have summaries yet (default: True)
            max_embed_text_chars: Passed through to chunk_and_embed_all — documents
                       whose text exceeds this are left in the backlog rather than
                       chunked/embedded this call. Lets a routine/cron run skip the
                       corpus's few giant documents (e.g. financial spreadsheets
                       running 10k+ chunks each) instead of one of them dominating
                       every incremental run's time budget.
            embed_images: Passed through to chunk_and_embed_all as include_images —
                       if False, image documents are left unchunked this run.
            embed_tables: Passed through to chunk_and_embed_all as include_tables —
                       if False, table documents are left unchunked this run.
            
        Returns:
            List of Document objects added to knowledge base
        """
        from . import run_log

        params = {
            "query": query,
            "max_results": max_results,
            "auto_embed": auto_embed,
            "auto_summarize": auto_summarize,
            "max_embed_text_chars": max_embed_text_chars,
            "embed_images": embed_images,
            "embed_tables": embed_tables,
        }
        run_id = run_log.start_run(self.source_id, params)
        # _process_items_sequential() reports progress on this row as it goes
        self._import_run_id = run_id
        stats: Dict[str, Any] = {"failures": [], "meta": {}}
        try:
            documents = self._process_all_impl(stats=stats, output_dir=output_dir, **params)
        except BaseException as e:
            # BaseException so Ctrl-C / SystemExit still close the row out
            # instead of leaving it "running" forever.
            status = "interrupted" if isinstance(e, KeyboardInterrupt) else "failed"
            run_log.finish_run(run_id, status, error=f"{type(e).__name__}: {e}", **stats)
            raise
        had_problems = bool(
            stats["failures"]
            or stats.get("summarize_errors")
            or stats.get("embed_errors")
        )
        run_log.finish_run(run_id, "partial" if had_problems else "success", **stats)
        return documents

    def _process_all_impl(
        self,
        stats: Dict[str, Any],
        query: Optional[str],
        max_results: Optional[int],
        output_dir: Optional[Path],
        auto_embed: bool,
        auto_summarize: bool,
        max_embed_text_chars: Optional[int],
        embed_images: bool,
        embed_tables: bool,
    ) -> List[Any]:
        """Body of process_all(); fills `stats` with ImportRun column values as it goes."""
        # Setup output directory
        # Use temporary directory by default - files will be copied to KB storage by ingest()
        use_temp_dir = output_dir is None
        if use_temp_dir:
            import tempfile
            # Create temp dir that will be cleaned up when process exits
            temp_dir = tempfile.mkdtemp(prefix=f"kb_import_{self.source_id}_")
            output_dir = Path(temp_dir)
            logger.debug(f"Using temporary download directory: {output_dir}")
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Fetching items from {self.name} (source_id: {self.source_id})")
        if query:
            logger.info(f"Query: {query}")
        
        # Fetch all items
        items = self.fetch_items(query=query, max_results=max_results)
        stats["items_found"] = len(items) if items else 0
        from . import run_log
        run_log.update_run(getattr(self, "_import_run_id", None), items_found=stats["items_found"])

        if not items:
            logger.warning(f"No items found for query: {query}")
            return []
        
        logger.info(f"Found {len(items)} item(s) to process")
        
        # Ensure source exists in database
        from ..kb import add_source, get_db_session
        
        with get_db_session() as session:
            try:
                add_source(
                    source_id=self.source_id,
                    name=self.name,
                    description=self.description,
                    base_uri=self.base_uri,
                    meta=self.meta,
                    session=session,
                )
            except Exception as e:
                logger.warning(f"Could not add/update source: {e}")
        
        # Process items sequentially
        # Note: Multi-threading doesn't help much here because:
        # - PDF parsing is CPU-bound (GIL limits benefit)
        # - Delay between requests means network isn't saturated
        # - Each worker would need its own DB session (overhead)
        documents = self._process_items_sequential(items, output_dir, max_results, stats=stats)
        stats["documents_created"] = len(documents)
        
        logger.info(f"Successfully processed {len(documents)} document(s)")
        
        # Optionally generate summaries for all documents for this source
        if auto_summarize:
            try:
                logger.info(f"Starting automatic summarization for source_id: {self.source_id}")
                from ..kb.tools import summarize_all
                summarize_result = summarize_all(
                    source_id=self.source_id,
                    create_summary_chunk=True,
                    embed_summary_chunk=True,  # Also embed summary chunks
                )
                logger.info(
                    f"Summarization complete: {summarize_result['summarized']} summarized, "
                    f"{summarize_result['chunked']} chunks created, "
                    f"{summarize_result['embedded']} chunks embedded, "
                    f"{summarize_result['errors']} errors"
                )
                stats["summarized"] = summarize_result["summarized"]
                stats["summarize_errors"] = summarize_result["errors"]
            except Exception as e:
                logger.error(f"Error during auto-summarize: {e}", exc_info=True)
                stats["failures"].append({"step": "auto-summarize", "error": f"{type(e).__name__}: {e}"})
        
        # Optionally chunk and embed all documents for this source
        if auto_embed:
            try:
                logger.info(f"Starting automatic chunking and embedding for source_id: {self.source_id}")
                from ..kb.tools import chunk_and_embed_all
                embed_result = chunk_and_embed_all(
                    source_id=self.source_id,
                    max_text_chars=max_embed_text_chars,
                    include_images=embed_images,
                    include_tables=embed_tables,
                )
                logger.info(
                    f"Embedding complete: {embed_result['chunked']} chunked, "
                    f"{embed_result['skipped']} skipped, {embed_result['errors']} errors"
                    + (
                        f", {embed_result['oversized_skipped']} left in backlog (too large)"
                        if max_embed_text_chars is not None else ""
                    )
                )
                stats["chunked"] = embed_result["chunked"]
                stats["embed_errors"] = embed_result["errors"]
                stats["embed_oversized"] = embed_result["oversized_skipped"]
                # Image/table sweep counts, when those sweeps ran
                stats["meta"]["embed"] = {
                    k: v for k, v in embed_result.items()
                    if k.startswith(("image_", "table_"))
                }
            except Exception as e:
                logger.error(f"Error during auto-embed: {e}", exc_info=True)
                stats["failures"].append({"step": "auto-embed", "error": f"{type(e).__name__}: {e}"})

        # Clean up temporary directory if we created one
        if use_temp_dir:
            try:
                import shutil
                shutil.rmtree(output_dir)
                logger.debug(f"Cleaned up temporary directory: {output_dir}")
            except Exception as e:
                logger.warning(f"Could not clean up temporary directory {output_dir}: {e}")

        return documents
    
    def _process_items_sequential(
        self,
        items: List[Dict[str, Any]],
        output_dir: Path,
        max_results: Optional[int] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Process items sequentially in a single thread.

        If `stats` is given, per-item counts (items_processed/skipped/failed)
        and a {"item_id", "error"} entry per failed item are recorded in it.

        Returns:
            List of document IDs that were created
        """
        from ..kb import get_db_session

        document_ids = []
        processed = 0
        skipped = 0
        errors = 0
        # Failed items go straight into stats, so an interrupted run still
        # records the failures seen so far.
        failures: List[Dict[str, str]] = stats["failures"] if stats is not None else []

        # Push live counts to the import-run row at most this often, so a
        # multi-hour run shows its progress in `kb logs imports`.
        from . import run_log
        run_id = getattr(self, "_import_run_id", None)
        progress_interval = 60.0
        last_progress = time.monotonic()

        # Commit after each document to ensure progress is saved
        # auto_commit=True will handle final cleanup (no-op since everything is already committed)
        with get_db_session() as session:
            for i, item in enumerate(items):
                if max_results and processed >= max_results:
                    break

                item_id = item.get("id", f"item-{i}")
                logger.info(f"Processing item {i+1}/{len(items)}: {item_id}")

                # An item skipped by skip_existing never reaches the server, so
                # it does not owe it a politeness delay. On a backfill those are
                # the majority of the list, and sleeping through them turns a
                # re-run over an already-imported window into hours of nothing.
                contacted_server = True
                try:
                    result = self.process_item(item, output_dir, session)

                    # Check if there was an error
                    if result.get("error"):
                        errors += 1
                        failures.append({"item_id": str(item_id), "error": str(result["error"])})
                        logger.warning(f"Item {item_id} failed: {result['error']}")
                        session.rollback()
                        continue

                    # Check if item was skipped (already exists)
                    if result.get("skipped"):
                        skipped += 1
                        contacted_server = False
                        logger.debug(f"Item {item_id} was skipped (already processed)")
                    else:
                        # Success - add document IDs
                        document_ids.extend(result.get("document_ids", []))
                        processed += 1

                    # Commit after each document to ensure progress is saved
                    # This is slower but safer - if an error occurs, we don't lose all progress
                    session.commit()

                except Exception as e:
                    errors += 1
                    failures.append({"item_id": str(item_id), "error": f"{type(e).__name__}: {e}"})
                    logger.error(f"Error processing item {item_id}: {e}", exc_info=True)
                    # Rollback on error to avoid leaving partial data
                    session.rollback()
                    continue

                finally:
                    if stats is not None:
                        stats["items_processed"] = processed
                        stats["items_skipped"] = skipped
                        stats["items_failed"] = errors
                        stats["documents_created"] = len(document_ids)
                        if run_id and time.monotonic() - last_progress >= progress_interval:
                            run_log.update_run(
                                run_id,
                                items_processed=processed,
                                items_skipped=skipped,
                                items_failed=errors,
                                documents_created=len(document_ids),
                                failures=failures,
                            )
                            last_progress = time.monotonic()
                    # Be polite - delay between requests
                    if contacted_server and self.delay > 0:
                        time.sleep(self.delay)

        if errors > 0:
            logger.warning(f"Encountered {errors} error(s) during processing")

        if skipped > 0:
            logger.info(f"Skipped {skipped} item(s) (already processed)")

        return document_ids
    

