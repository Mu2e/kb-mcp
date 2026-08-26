#!/usr/bin/env python3
"""MediaWiki importer for fetching wiki pages using the MediaWiki API.

Supports Kerberos/SPNEGO authentication via curl --negotiate for
sites like mu2ewiki.fnal.gov that require FNAL SSO.
"""

import json
import logging
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from .base import Source
from ..kb import add_document

logger = logging.getLogger(__name__)


def parse_wiki_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Convert a MediaWiki API timestamp to an aware UTC datetime.

    MediaWiki returns ISO-8601 with a trailing "Z" (e.g.
    "2016-12-28T22:34:43Z"), which fromisoformat() only accepts directly
    from Python 3.11 on; the replacement keeps this working on older
    interpreters too.

    Returns None for missing or unparseable values — timestamps are
    nice-to-have metadata and must never fail an import.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        logger.warning(f"Could not parse wiki timestamp {value!r}")
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MediaWikiSource(Source):
    """Importer for MediaWiki sites using the MediaWiki API.

    Uses curl with --negotiate for Kerberos/SPNEGO authentication,
    which is required for FNAL wikis.
    """

    def __init__(
        self,
        wiki_url: str = "https://mu2ewiki.fnal.gov",
        source_id: str = "mu2e-wiki",
        delay: float = 0.5,
        timeout: float = 30.0,
        skip_existing: bool = False,
        use_kerberos: bool = True,
    ):
        """Initialize the MediaWiki source.

        Args:
            wiki_url: Base URL of the wiki (e.g., "https://mu2ewiki.fnal.gov")
            source_id: Source identifier for knowledge base
            delay: Delay between requests in seconds
            timeout: Request timeout in seconds
            skip_existing: If True, skip pages already in the database
            use_kerberos: If True, use Kerberos auth via curl --negotiate
        """
        super().__init__(
            source_id=source_id,
            name="mediawiki",
            description=f"MediaWiki: {wiki_url}",
            base_uri=wiki_url,
            delay=delay,
            timeout=timeout,
            meta={"scraper": "mediawiki.py", "wiki_url": wiki_url},
        )
        self.wiki_url = wiki_url.rstrip("/")
        self.api_url = f"{self.wiki_url}/w/api.php"
        self.skip_existing = skip_existing
        self.use_kerberos = use_kerberos

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _api_request(self, params: Dict[str, str]) -> Optional[Dict]:
        """Make a MediaWiki API request using curl.

        Uses curl --negotiate for Kerberos authentication.

        Args:
            params: Query parameters for the API request

        Returns:
            JSON response as dict, or None if request failed
        """
        params["format"] = "json"

        # Build URL with query params. urlencode() percent-escapes the values,
        # which matters for page titles containing &, =, #, + or spaces - those
        # would otherwise corrupt the query string.
        query_string = urlencode(params)
        url = f"{self.api_url}?{query_string}"

        cmd = ["curl", "-s", "--max-time", str(int(self.timeout))]
        if self.use_kerberos:
            cmd.extend(["--negotiate", "-u", ":"])
        cmd.append(url)

        try:
            logger.debug(f"API request: {url}")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout + 5
            )
            if result.returncode != 0:
                logger.error(f"curl failed (rc={result.returncode}): {result.stderr}")
                return None

            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            logger.error(f"Request timed out: {url}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from {url}: {e}")
            logger.debug(f"Response was: {result.stdout[:500]}")
            return None

    def _get_revision_times(self, title: str) -> Dict[str, Optional[datetime]]:
        """Fetch a page's first and last revision timestamps.

        This needs its own API calls: the `action=parse` request used for
        content carries no revision data, and `rvdir` only walks one
        direction, so the oldest and newest revisions cannot be fetched
        together. Two cheap `action=query` calls (rvlimit=1 each) is the
        supported way to get both.

        The last-edit time comes from the newest revision rather than the
        page's `touched` field: `touched` also moves on cache invalidation
        and template edits, so it overstates when the page itself changed.

        Never raises — a page whose history is unavailable simply imports
        without timestamps.
        """
        times: Dict[str, Optional[datetime]] = {"created": None, "updated": None}
        page_title = title.replace(" ", "_")

        for key, direction in (("created", "newer"), ("updated", "older")):
            data = self._api_request(
                {
                    "action": "query",
                    "prop": "revisions",
                    "titles": page_title,
                    "rvprop": "timestamp",
                    "rvlimit": "1",
                    "rvdir": direction,
                }
            )
            if not data or "error" in data:
                continue
            pages = (data.get("query") or {}).get("pages") or {}
            for page in pages.values():
                # Missing/deleted pages come back with no "revisions" key.
                revisions = page.get("revisions") or []
                if revisions:
                    times[key] = parse_wiki_timestamp(revisions[0].get("timestamp"))

        return times

    def _get_page_content(self, title: str) -> Optional[Dict[str, Any]]:
        """Fetch parsed HTML content and metadata for a wiki page.

        Args:
            title: Wiki page title (e.g., "Main_Page", "Computing")

        Returns:
            Dictionary with page content and metadata, or None if failed
        """
        # Use action=parse to get rendered HTML and metadata
        data = self._api_request(
            {
                "action": "parse",
                "page": title.replace(" ", "_"),
                "prop": "text|categories|links|sections|displaytitle",
            }
        )

        if not data or "error" in data:
            error_msg = data.get("error", {}).get("info", "Unknown error") if data else "No response"
            logger.error(f"Failed to fetch page '{title}': {error_msg}")
            return None

        parse_data = data.get("parse", {})
        html_content = parse_data.get("text", {}).get("*", "")
        # displaytitle can contain HTML tags like <span>; strip them
        raw_title = parse_data.get("displaytitle", title)
        display_title = re.sub(r"<[^>]+>", "", raw_title).strip() or title
        page_id = parse_data.get("pageid", 0)
        sections = parse_data.get("sections", [])
        categories = [
            cat.get("*", "") for cat in parse_data.get("categories", [])
        ]
        internal_links = [
            link.get("*", "")
            for link in parse_data.get("links", [])
            if link.get("ns", -1) == 0  # Only main namespace pages
        ]

        return {
            "title": display_title,
            "page_id": page_id,
            "html": html_content,
            "sections": sections,
            "categories": categories,
            "internal_links": internal_links,
        }

    def _list_all_pages(self, limit: int = 500) -> List[Dict[str, Any]]:
        """List all pages in the wiki main namespace.

        Args:
            limit: Maximum number of pages per API call (max 500)

        Returns:
            List of page info dicts with 'pageid', 'title' fields
        """
        all_pages = []
        params = {
            "action": "query",
            "list": "allpages",
            "aplimit": str(min(limit, 500)),
            "apnamespace": "0",  # Main namespace only
        }

        while True:
            data = self._api_request(params)
            if not data:
                break

            pages = data.get("query", {}).get("allpages", [])
            all_pages.extend(pages)
            logger.info(f"Fetched {len(all_pages)} page titles so far...")

            # Check for continuation
            cont = data.get("continue")
            if cont and "apcontinue" in cont:
                params["apcontinue"] = cont["apcontinue"]
                params["continue"] = cont.get("continue", "-||")
                time.sleep(self.delay)
            else:
                break

        return all_pages

    def _list_category_pages(
        self, category: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """List all pages in a specific category.

        Args:
            category: Category name (without "Category:" prefix)
            limit: Maximum number of pages

        Returns:
            List of page info dicts
        """
        all_pages = []
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{category}",
            "cmlimit": str(min(limit, 500)),
            "cmtype": "page",
        }

        while True:
            data = self._api_request(params)
            if not data:
                break

            pages = data.get("query", {}).get("categorymembers", [])
            all_pages.extend(pages)

            cont = data.get("continue")
            if cont and "cmcontinue" in cont:
                params["cmcontinue"] = cont["cmcontinue"]
                params["continue"] = cont.get("continue", "-||")
                time.sleep(self.delay)
            else:
                break

        return all_pages

    def _list_linked_pages(self, title: str) -> List[str]:
        """List all pages linked from a given page.

        Args:
            title: Page title to get links from

        Returns:
            List of linked page titles
        """
        content = self._get_page_content(title)
        if not content:
            return []
        return content.get("internal_links", [])

    def fetch_items(
        self,
        query: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch page items from the wiki.

        The query string controls what pages to fetch:
        - Comma-separated page titles: "Main_Page,Computing,Practicalities"
        - "links:PageTitle" - all pages linked from PageTitle
        - "category:CategoryName" - all pages in a category
        - "all" - all pages in the wiki
        - None or empty - defaults to "Main_Page"

        Args:
            query: Query string (see above)
            max_results: Maximum number of pages to fetch

        Returns:
            List of item dicts with 'id' and 'title' fields
        """
        if not query:
            query = "Main_Page"

        items = []

        if query == "all":
            # List all pages
            pages = self._list_all_pages()
            items = [
                {"id": str(p["pageid"]), "title": p["title"]} for p in pages
            ]

        elif query.startswith("links:"):
            # Get pages linked from a specific page
            source_page = query[6:].strip()
            logger.info(f"Fetching pages linked from '{source_page}'")
            linked_titles = self._list_linked_pages(source_page)
            # Include the source page itself
            all_titles = [source_page] + linked_titles
            items = [
                {"id": title.replace(" ", "_"), "title": title}
                for title in all_titles
            ]

        elif query.startswith("category:"):
            # Get pages in a category
            category = query[9:].strip()
            logger.info(f"Fetching pages in category '{category}'")
            pages = self._list_category_pages(category)
            items = [
                {"id": str(p["pageid"]), "title": p["title"]} for p in pages
            ]

        else:
            # Comma-separated list of page titles
            titles = [t.strip() for t in query.split(",") if t.strip()]
            items = [
                {"id": title.replace(" ", "_"), "title": title}
                for title in titles
            ]

        # Apply max_results limit
        if max_results and len(items) > max_results:
            items = items[:max_results]

        logger.info(f"Found {len(items)} page(s) to process")
        return items

    def process_item(
        self,
        item: Dict[str, Any],
        output_dir: Path,
        session: Any,
    ) -> Dict[str, Any]:
        """Process a single wiki page.

        Fetches the page content as HTML, saves it to a temporary file,
        and adds it to the knowledge base using the existing HTML parser.

        Args:
            item: Item dict with 'id' and 'title' fields
            output_dir: Directory to save downloaded files
            session: Database session

        Returns:
            Processing result dictionary
        """
        title = item.get("title", "")
        doc_id = title.replace(" ", "_")

        if not title:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": "Empty page title",
            }

        # Check if already exists
        if self.skip_existing:
            from ..kb.db_models import RawDocument

            existing = (
                session.query(RawDocument)
                .filter(
                    RawDocument.source_id == self.source_id,
                    RawDocument.doc_id == doc_id,
                )
                .first()
            )
            if existing:
                logger.info(
                    f"Skipping '{title}' - already exists (raw_document_id: {existing.id})"
                )
                return {
                    "document_ids": [],
                    "num_documents": 0,
                    "parsed": False,
                    "raw_document_id": existing.id,
                    "skipped": True,
                    "error": None,
                }

        # Fetch page content
        logger.info(f"Fetching wiki page: {title}")
        content = self._get_page_content(title)

        if not content or not content.get("html"):
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"Failed to fetch content for page '{title}'",
            }

        # Wrap in a minimal HTML document for the parser
        html = f"""<!DOCTYPE html>
<html>
<head><title>{content['title']}</title></head>
<body>
<h1>{content['title']}</h1>
{content['html']}
</body>
</html>"""

        # Save as HTML file
        safe_filename = doc_id.replace("/", "_").replace("\\", "_")
        html_path = output_dir / f"{safe_filename}.html"
        html_path.write_text(html, encoding="utf-8")

        # Build metadata
        uri = f"{self.wiki_url}/wiki/{doc_id}"
        metadata = {
            "title": content["title"],
            "page_id": content.get("page_id"),
            "categories": content.get("categories", []),
            "sections": [s.get("line", "") for s in content.get("sections", [])],
            "wiki_url": self.wiki_url,
        }

        # Revision history, promoted to the Document columns so search and
        # the web UI can filter on when the page was written and last edited
        # rather than when we happened to scrape it.
        revision_times = self._get_revision_times(title)
        if revision_times["created"]:
            metadata["wiki_created"] = revision_times["created"].isoformat()
        if revision_times["updated"]:
            metadata["wiki_last_edit"] = revision_times["updated"].isoformat()

        # Add to knowledge base
        result = add_document(
            html_path,
            source_id=self.source_id,
            doc_id=doc_id,
            uri=uri,
            meta=metadata,
            creating_time=revision_times["created"],
            update_time=revision_times["updated"],
            copy_to_kb=True,
            session=session,
        )

        result["error"] = None
        logger.info(
            f"Added wiki page '{title}': {result.get('num_documents', 0)} document(s)"
        )
        return result
