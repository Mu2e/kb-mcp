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
            
        Returns:
            List of Document objects added to knowledge base
        """
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
        documents = self._process_items_sequential(items, output_dir, max_results)
        
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
            except Exception as e:
                logger.error(f"Error during auto-summarize: {e}", exc_info=True)
        
        # Optionally chunk and embed all documents for this source
        if auto_embed:
            try:
                logger.info(f"Starting automatic chunking and embedding for source_id: {self.source_id}")
                from ..kb.tools import chunk_and_embed_all
                embed_result = chunk_and_embed_all(source_id=self.source_id)
                logger.info(
                    f"Embedding complete: {embed_result['chunked']} chunked, "
                    f"{embed_result['skipped']} skipped, {embed_result['errors']} errors"
                )
            except Exception as e:
                logger.error(f"Error during auto-embed: {e}", exc_info=True)

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
    ) -> List[str]:
        """Process items sequentially in a single thread.

        Returns:
            List of document IDs that were created
        """
        from ..kb import get_db_session

        document_ids = []
        processed = 0
        skipped = 0
        errors = 0

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
                    logger.error(f"Error processing item {item_id}: {e}", exc_info=True)
                    # Rollback on error to avoid leaving partial data
                    session.rollback()
                    continue

                finally:
                    # Be polite - delay between requests
                    if contacted_server and self.delay > 0:
                        time.sleep(self.delay)

        if errors > 0:
            logger.warning(f"Encountered {errors} error(s) during processing")

        if skipped > 0:
            logger.info(f"Skipped {skipped} item(s) (already processed)")

        return document_ids
    

