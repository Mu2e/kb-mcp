#!/usr/bin/env python3
"""INSPIRE-HEP importer for fetching PDFs and metadata using the API."""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode

# Add project root to path if running as script
_IS_MAIN = __name__ == "__main__"
if _IS_MAIN:
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

import httpx
from dotenv import load_dotenv

load_dotenv()

# Use absolute imports when running as script, relative when imported as module
if _IS_MAIN:
    from kb_mcp.imports.base import Source
    from kb_mcp.kb import add_document
else:
    from .base import Source
    from ..kb import add_document


logger = logging.getLogger(__name__)


class InspireSource(Source):
    """Importer for INSPIRE-HEP literature database using the REST API."""
    
    def __init__(
        self,
        base_url: str = "https://inspirehep.net",
        api_base_url: str = "https://inspirehep.net/api",
        source_id: str = "inspire-hep",
        delay: float = 0.5,
        timeout: float = 30.0,
        skip_existing: bool = False,
    ):
        """Initialize the INSPIRE-HEP source.

        Args:
            base_url: Base URL for INSPIRE-HEP website
            api_base_url: Base URL for INSPIRE-HEP API
            source_id: Source identifier for knowledge base
            delay: Delay between requests in seconds (to be polite)
            timeout: Request timeout in seconds
            skip_existing: If True, check if (source_id, doc_id) exists in RawDocuments before downloading.
                          This saves bandwidth by not re-downloading files already in the database.
                          Default: False (always download)
        """
        super().__init__(
            source_id=source_id,
            name="inspire-hep",
            description="INSPIRE-HEP Literature Database",
            base_uri=base_url,
            delay=delay,
            timeout=timeout,
            meta={"scraper": "inspire.py", "api": True},
        )
        self.base_url = base_url.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.skip_existing = skip_existing
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
            logger.error(f"API request failed: {url} - {e}")
            return None
    
    # INSPIRE-HEP literature endpoint caps `size` at 250 per request and
    # rejects values above that. To return more than 250 hits we have to
    # paginate via the `page` parameter.
    _PAGE_SIZE_CAP = 250

    def fetch_items(self, query: Optional[str] = None, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch records from INSPIRE-HEP API, paginating as needed.

        Args:
            query: Direct search query (e.g., "collaborations.value:Mu2e").
                Note: use ElasticSearch fielded syntax — the SPIRES-legacy
                `find exp mu2e` form returns 0 hits on the modern API.
            max_results: Soft cap on number of hits to return. The API
                paginates 250 at a time; this function loops over `page`
                until either max_results items have been collected or the
                server reports no more hits. ``None`` means "fetch all
                hits the query returns".

        Returns:
            List of hit dictionaries from the API, length ≤ max_results
            and ≤ the query's actual total-hits count.
        """
        if max_results is None:
            target = float("inf")
        else:
            target = max_results

        # Important: Inspire's `page` parameter is page-size-relative
        # (skip = (page-1) * size). We MUST hold `size` constant across
        # all pages or page 2 will overlap page 1 — caught 2026-04-27.
        # Trim to max_results at the end instead of shrinking the last
        # page's size.
        page_size = min(self._PAGE_SIZE_CAP, int(target)) if target != float("inf") else self._PAGE_SIZE_CAP

        logger.info(f"Searching INSPIRE-HEP API with query: {query}")
        hits: List[Dict[str, Any]] = []
        page = 1
        while len(hits) < target:
            page_resp = self._api_request(
                "/literature",
                params={"q": query, "size": page_size, "page": page},
            )
            if not page_resp:
                break
            page_hits = page_resp.get("hits", {}).get("hits", [])
            if not page_hits:
                break  # Server returned nothing more — done
            hits.extend(page_hits)
            # If the server gave us fewer than the page size, we've drained
            # the result set (no further pages exist).
            if len(page_hits) < page_size:
                break
            page += 1

        # Trim to caller's max_results — we may have overshot by up to
        # page_size - 1 hits because we held size constant.
        if max_results is not None:
            hits = hits[:max_results]

        logger.info(f"Found {len(hits)} result(s) across {page} page(s)")
        return hits
    
    def _extract_metadata_from_api(self, record_data: Dict) -> Dict:
        """Extract metadata from INSPIRE-HEP API record.
        
        Args:
            record_data: Metadata dictionary from API response
            
        Returns:
            Dictionary with extracted metadata
        """
        metadata = {}
        
        # Title
        titles = record_data.get("titles", [])
        if titles:
            metadata["title"] = titles[0].get("title", "")
        
        # Authors
        authors = record_data.get("authors", [])
        if authors:
            author_names = []
            for author in authors:
                full_name = author.get("full_name", "")
                if full_name:
                    author_names.append(full_name)
            if author_names:
                metadata["authors"] = author_names
        
        # Abstract
        abstracts = record_data.get("abstracts", [])
        if abstracts:
            metadata["abstract"] = abstracts[0].get("value", "")
        
        # Publication date
        preprint_date = record_data.get("preprint_date")
        if preprint_date:
            metadata["publication_date"] = preprint_date
        
        # Record ID
        control_number = record_data.get("control_number")
        if control_number:
            metadata["record_id"] = str(control_number)
        
        # ArXiv ID
        arxiv_eprints = record_data.get("arxiv_eprints", [])
        if arxiv_eprints:
            metadata["arxiv_id"] = arxiv_eprints[0].get("value", "")
        
        return metadata
    
    def _download_pdf(self, pdf_url: str, output_dir: Path, record_id: str) -> Optional[Path]:
        """Download a PDF from a URL.
        
        Args:
            pdf_url: URL of the PDF to download
            output_dir: Directory to save the PDF
            record_id: Record ID for filename
            
        Returns:
            Path to downloaded PDF, or None if download failed
        """
        try:
            logger.debug(f"Downloading PDF from {pdf_url}")
            response = self.client.get(pdf_url)
            response.raise_for_status()
            
            # Create filename from record_id
            filename = f"inspire-{record_id}.pdf"
            output_path = output_dir / filename
            
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
    
    def process_item(
        self,
        item: Dict[str, Any],
        output_dir: Path,
        session: Any,
    ) -> Dict[str, Any]:
        """Process a single INSPIRE-HEP record.

        Args:
            item: Hit dictionary from API (contains 'id' and 'metadata')
            output_dir: Directory to save downloaded PDFs
            session: Database session for adding documents

        Returns:
            Dictionary with processing results including document_ids, num_documents, parsed, and error fields
        """
        record_data = item.get("metadata", {})
        if not record_data:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"No metadata found for item {item.get('id', 'unknown')}"
            }

        record_id = str(item.get("id", ""))

        # Skip records without documents
        if not record_data.get('documents') or len(record_data.get('documents', [])) == 0:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"No documents available for record {record_id}"
            }

        # Check if document already exists before downloading (if skip_existing=True)
        if self.skip_existing:
            from ..kb.db_models import RawDocument
            existing_raw = session.query(RawDocument).filter(
                RawDocument.source_id == self.source_id,
                RawDocument.doc_id == record_id
            ).first()

            if existing_raw:
                logger.info(f"Skipping record {record_id} - already exists in database (raw_document_id: {existing_raw.id})")
                return {
                    "document_ids": [],
                    "num_documents": 0,
                    "parsed": False,
                    "raw_document_id": existing_raw.id,
                    "skipped": True,
                    "error": None
                }

        # Extract metadata
        metadata = self._extract_metadata_from_api(record_data)
        pdf_url = record_data['documents'][0]['url']
        uri = f"https://inspirehep.net/literature/{record_id}"

        # Download PDF
        pdf_path = self._download_pdf(pdf_url, output_dir, record_id)
        if not pdf_path:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"Failed to download PDF for record {record_id}"
            }

        # Add to knowledge base (let ingest copy file to KB storage)
        result = add_document(
            pdf_path,
            source_id=self.source_id,
            doc_id=record_id,
            uri=uri,
            meta=metadata,
            copy_to_kb=True,  # Copy from temp dir to data/sources/inspire-hep/
            session=session
        )

        # Debug: log what we got back
        logger.debug(f"add_document returned: document_ids={result.get('document_ids', [])}, num_documents={result.get('num_documents', 0)}, parsed={result.get('parsed', False)}")

        # Add error field (None = success)
        result["error"] = None
        return result


# Main entry point moved to imports/cli.py
# This file can still be run directly for backward compatibility
if __name__ == "__main__":
    import argparse
    
    # Import based on whether we're running as script or module
    if _IS_MAIN:
        from kb_mcp.imports.cli import cmd_inspire
    else:
        from .cli import cmd_inspire
    
    parser = argparse.ArgumentParser(
        description="Fetch documents from INSPIRE-HEP using the API"
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
        help="Directory to save downloaded PDFs (default: data/sources/inspire-hep)",
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
        help="Disable automatic chunking and embedding after processing",
    )
    parser.add_argument(
        "--no-auto-summarize",
        action="store_true",
        help="Disable automatic summarization after processing",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip documents that already exist in the database (check by source_id and doc_id before downloading). "
             "This saves bandwidth by not re-downloading files. Default: False (always download)",
    )

    args = parser.parse_args()
    cmd_inspire(args)

