#!/usr/bin/env python3
"""Scrape INSPIRE-HEP for PDFs and metadata using the API, then add to knowledge base."""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

# Add project root to path if running as script
_IS_MAIN = __name__ == "__main__"
if _IS_MAIN:
    # Get the directory containing this file
    script_dir = Path(__file__).parent
    # Go up to project root: src/test_mcp/scrape -> src/test_mcp -> src -> project_root
    project_root = script_dir.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

try:
    import httpx
except ImportError:
    raise ImportError(
        "httpx is required for scraping. Install with: pip install httpx"
    )

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Use absolute imports when running as script, relative when imported as module
if _IS_MAIN:
    from test_mcp.kb import add_source, add_from_path, get, get_db_session
    from test_mcp.kb.core import Document
    from test_mcp.kb.tools import chunk_and_embed_all
else:
    from ..kb import add_source, add_from_path, get, get_db_session
    from ..kb.core import Document
    from ..kb.tools import chunk_and_embed_all


logger = logging.getLogger(__name__)


class InspireScraper:
    """Scraper for INSPIRE-HEP literature database using the REST API."""
    
    def __init__(
        self,
        base_url: str = "https://inspirehep.net",
        api_base_url: str = "https://inspirehep.net/api",
        source_id: str = "inspire-hep",
        delay: float = 0.5,
        timeout: float = 30.0,
    ):
        """Initialize the INSPIRE-HEP scraper.
        
        Args:
            base_url: Base URL for INSPIRE-HEP website
            api_base_url: Base URL for INSPIRE-HEP API
            source_id: Source identifier for knowledge base
            delay: Delay between requests in seconds (to be polite)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.source_id = source_id
        self.delay = delay
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client.close()
    
    def _api_request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make an API request and return JSON response.
        
        Args:
            endpoint: API endpoint (e.g., "/literature" or "/literature/1234567")
            params: Optional query parameters
            
        Returns:
            JSON response as dict, or None if request failed
        """
        url = f"{self.api_base_url}{endpoint}"
        try:
            logger.debug(f"API request: {url} with params {params}")
            response = self.client.get(url, params=params)
            response.raise_for_status()
            time.sleep(self.delay)  # Be polite
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from {url}: {e}")
            return None
    
    
    def _get_literature_record(self, record_id: str) -> Optional[Dict]:
        """Get a single literature record by ID.
        
        Args:
            record_id: Literature record ID
            
        Returns:
            Record data as dict, or None if failed
        """
        return self._api_request(f"/literature/{record_id}")
    
    def _extract_metadata_from_api(self, record_data: Dict) -> Dict:
        """Extract metadata from API response.
        
        Args:
            record_data: Record data from API response
            
        Returns:
            Dictionary with metadata
        """
        metadata = {
            "record_id": str(record_data.get("control_number", "")),
            "authors": []
        }
        
        # Extract title
        titles = record_data.get("titles", [])
        if titles:
            metadata["title"] = titles[0].get("title", "")
        
        # Extract authors
        authors = record_data.get("authors", [])
        for author in authors:
            full_name = author.get("full_name", "")
            if full_name:
                metadata["authors"].append(full_name)
        
        # Extract abstract
        abstracts = record_data.get("abstracts", [])
        if abstracts:
            metadata["abstract"] = abstracts[0].get("value", "")
        
        # Publciation date
        preprint_date = record_data.get("preprint_date", "")
        if preprint_date:
           metadata["publication_date"] = preprint_date


        # Extract publication date
        publication_info = record_data.get("publication_info", [])
        if publication_info:
            pub_date = publication_info[0]
            year = pub_date.get("year")
            if year:
                metadata["publication_date"] = str(year)
        
        # Extract collaboration
        collaborations = record_data.get("collaborations", [])
        if collaborations:
            metadata["collaboration"] = collaborations[0].get("value", "")
        
        # Extract citation count
        citation_count = record_data.get("citation_count", 0)
        if citation_count:
            metadata["citation_count"] = citation_count
        
        return metadata
    
    def _download_pdf(self, pdf_url: str, output_dir: Path, doc_id: str) -> Optional[Path]:
        """Download PDF file.
        
        Args:
            pdf_url: URL of PDF to download
            output_dir: Directory to save PDF
            
        Returns:
            Path to downloaded file or None if download failed
        """
        try:
            logger.debug(f"Downloading PDF: {pdf_url}")
            response = self.client.get(pdf_url)
            response.raise_for_status()
            
            # Check if response is actually a PDF
            content_type = response.headers.get("content-type", "")
            if "application/pdf" not in content_type:
                logger.warning(f"Response from {pdf_url} is not a PDF (content-type: {content_type})")
                return None
            
            output_path = output_dir / f"{self.source_id}_{doc_id}.pdf"
            
            # Handle filename conflicts
            counter = 1
            original_path = output_path
            while output_path.exists():
                stem = original_path.stem
                output_path = output_dir / f"{stem}_{counter}.pdf"
                counter += 1
            
            # Save PDF
            output_path.write_bytes(response.content)
            logger.info(f"Downloaded PDF: {output_path}")
            time.sleep(self.delay)  
            
            return output_path
            
        except httpx.HTTPError as e:
            logger.error(f"Error downloading PDF from {pdf_url}: {e}")
            return None
    
    def scrape_search_results(
        self,
        query: Optional[str] = None,
        max_results: Optional[int] = None,
        output_dir: Optional[Path] = None,
        auto_embed: bool = True,
    ) -> List[Document]:
        """Scrape search results using API and add documents to knowledge base.
        
        Args:
            query: Direct search query (e.g., "collaboration:SLD")
            max_results: Maximum number of results to process (None for all, defaults to 1000)
            output_dir: Directory to save downloaded PDFs (default: data/local/inspire)
            auto_embed: If True, automatically chunk and embed all documents for this source
                       that don't have chunks yet (default: True)
            
        Returns:
            List of Document objects added to knowledge base
        """
        # Setup output directory
        if output_dir is None:
            data_dir = os.getenv("DATA_DIR", "data")
            output_dir = Path(data_dir) / "local" / "inspire"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Searching INSPIRE-HEP API with query: {query}")
    
        # Fetch results using API
        if max_results is None:
            max_results = 1000
        all_records = self._api_request("/literature" , params={"q": query, "size": max_results})
        hits = all_records.get("hits", {}).get("hits", [])

        logger.info(f"Found {len(hits)} results")
        n_hits = len(hits)
        if n_hits == 0:   
            logger.warning(f"No results found for query: {query}")
            return []

        documents = []
        with get_db_session() as session:
            # Ensure source exists (within the same transaction)
            try:
                add_source(
                    source_id=self.source_id,
                    name="inspire-hep",
                    description="INSPIRE-HEP Literature Database",
                    base_uri=self.base_url,
                    meta={"scraper": "inspire.py", "api": True},
                    session=session,
                )
            except Exception as e:
                logger.warning(f"Could not add/update source: {e}")
            docs_added = 0
            for i, hit in enumerate(hits):
                if max_results and i >= max_results:
                    break
                record_data = hit.get("metadata", {})
                if record_data:
                    record_id = str(hit.get("id", ""))
                    logger.info(f"Processing record {i+1}/{n_hits}: {record_id}")
                    
                    # Skip records without documents
                    if not record_data.get('documents') or len(record_data.get('documents', [])) == 0:
                        logger.warning(f"Skipping record {record_id}: no documents available")
                        continue
                    
                    metadata = self._extract_metadata_from_api(record_data)
                    pdf_url = record_data['documents'][0]['url']
                    uri = f"https://inspirehep.net/literature/{record_id}"

                    # download pdf
                    pdf_path = self._download_pdf(pdf_url, output_dir, record_id)
                    if not pdf_path:
                        logger.warning(f"Failed to download PDF for record {record_id}")
                        continue

                    # add to knowledge base
                    doc_list = add_from_path(
                        pdf_path,
                        data={
                            "source_id": self.source_id,
                            "doc_id": record_id,
                            "uri": uri,
                            "source_type": "application/pdf",
                            "meta": metadata,
                        },
                        session=session
                    )
                    if doc_list:
                        documents.extend(doc_list)
                        docs_added += 1
            
            # Expunge all documents from session before it closes
            # This prevents DetachedInstanceError when accessing attributes later
            # (get_db_session() will automatically commit when the with block exits)
            session.expunge_all()
        
        logger.info(f"Successfully added {docs_added} document(s) to knowledge base")
        
        # Optionally chunk and embed all documents for this source
        if auto_embed:
            try:
                logger.info(f"Starting automatic chunking and embedding for source_id: {self.source_id}")
                embed_result = chunk_and_embed_all(
                    source_id=self.source_id,
                )
                logger.info(
                    f"Embedding complete: {embed_result['chunked']} chunked, "
                    f"{embed_result['skipped']} skipped, {embed_result['errors']} errors"
                )
            except ImportError as e:
                logger.warning(f"Embedding module not available, skipping auto-embed: {e}")
            except Exception as e:
                logger.error(f"Error during auto-embed: {e}", exc_info=True)
        
        return documents


def main():
    """Main entry point for INSPIRE-HEP scraper."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Scrape INSPIRE-HEP for PDFs and metadata using the API"
    )
    parser.add_argument(
        "--query",
        "-q",
        default="collaboration:SLD",
        help="Direct search query (e.g., 'collaboration:SLD').",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        help="Maximum number of results to process (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save downloaded PDFs (default: data/local/inspire)",
    )
    parser.add_argument(
        "--source-id",
        default="inspire-hep",
        help="Source identifier for knowledge base (default: inspire-hep)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay between requests in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--no-auto-embed",
        action="store_true",
        help="Disable automatic chunking and embedding after scraping",
    )
    
    args = parser.parse_args()
    
    # Setup logging format for all loggers (via root logger)
    root_log_level = logging.DEBUG if args.verbose else logging.INFO
    
    # Configure root logger format for all loggers
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        # No handlers configured yet, use basicConfig
        logging.basicConfig(
            #level=root_log_level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%H:%M:%S",
        )
    
    # Set this module's logger level to INFO (regardless of verbose flag)
    logger.setLevel(logging.INFO)
    
    # Run scraper
    with InspireScraper(
        source_id=args.source_id,
        delay=args.delay,
    ) as scraper:
        documents = scraper.scrape_search_results(
            query=args.query,
            max_results=args.max_results,
            output_dir=args.output_dir,
            auto_embed=not args.no_auto_embed,
        )
        
        print(f"\n  Successfully processed {len(documents)} document(s)")
        for doc in documents:
            print(f"  - {doc.id}: {doc.source_id}/{doc.doc_id}")


if __name__ == "__main__":
    main()
