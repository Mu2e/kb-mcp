"""Base class for document importers."""

import logging
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Any

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
    ) -> Optional[List[Any]]:
        """Process a single item and add it to the knowledge base.
        
        Args:
            item: Item dictionary from fetch_items()
            output_dir: Directory to save downloaded files
            session: Database session for adding documents
            
        Returns:
            List of Document objects added, or None if processing failed
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
            output_dir: Directory to save downloaded files (default: data/local/{source_id})
            auto_embed: If True, automatically chunk and embed all documents for this source
                       that don't have chunks yet (default: True)
            auto_summarize: If True, automatically generate summaries for all documents for this source
                           that don't have summaries yet (default: True)
            
        Returns:
            List of Document objects added to knowledge base
        """
        # Setup output directory
        if output_dir is None:
            data_dir = os.getenv("DATA_DIR", "data")
            output_dir = Path(data_dir) / "local" / self.source_id
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
            except ImportError as e:
                logger.warning(f"Embedding module not available, skipping auto-embed: {e}")
            except Exception as e:
                logger.error(f"Error during auto-embed: {e}", exc_info=True)
        
        # Optionally generate summaries for all documents for this source
        if auto_summarize:
            try:
                logger.info(f"Starting automatic summarization for source_id: {self.source_id}")
                from ..kb.tools import summarize_all
                summarize_result = summarize_all(source_id=self.source_id)
                logger.info(
                    f"Summarization complete: {summarize_result['summarized']} summarized, "
                    f"{summarize_result['chunked']} chunks created, "
                    f"{summarize_result['errors']} errors"
                )
            except ImportError as e:
                logger.warning(f"Summary module not available, skipping auto-summarize: {e}")
            except Exception as e:
                logger.error(f"Error during auto-summarize: {e}", exc_info=True)
        
        return documents
    
    def _process_items_sequential(
        self,
        items: List[Dict[str, Any]],
        output_dir: Path,
        max_results: Optional[int] = None,
    ) -> List[Any]:
        """Process items sequentially in a single thread."""
        from ..kb import get_db_session
        
        documents = []
        processed = 0
        errors = 0
        
        with get_db_session() as session:
            for i, item in enumerate(items):
                if max_results and processed >= max_results:
                    break
                
                item_id = item.get("id", f"item-{i}")
                logger.info(f"Processing item {i+1}/{len(items)}: {item_id}")
                
                try:
                    doc_list = self.process_item(item, output_dir, session)
                    if doc_list:
                        documents.extend(doc_list)
                        processed += 1
                    else:
                        logger.warning(f"Item {item_id} returned no documents")
                except Exception as e:
                    errors += 1
                    logger.error(f"Error processing item {item_id}: {e}", exc_info=True)
                    continue
                
                # Be polite - delay between requests
                if self.delay > 0:
                    time.sleep(self.delay)
            
            # Expunge all documents from session before it closes
            session.expunge_all()
        
        if errors > 0:
            logger.warning(f"Encountered {errors} error(s) during processing")
        
        return documents
    

