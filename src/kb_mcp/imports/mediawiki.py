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

# mu2ewiki.fnal.gov sits behind Cloudflare, which silently drops requests
# carrying curl's default User-Agent; a browser-shaped one is answered.
_BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _strip_edit_section_links(html_fragment: str) -> str:
    """Remove MediaWiki's `[edit]` section-edit links from parsed page HTML.

    `action=parse` renders every heading with a trailing
    `<span class="mw-editsection">[edit]</span>` — a UI affordance for
    editing that section in the wiki, not part of the page's actual
    content. Left in, it survives Docling's HTML→Markdown conversion as
    `[Section Title [ edit ]](/w/index.php?...&action=edit&section=N)`,
    polluting every section heading (and therefore every `section_path`
    derived from it) with wiki chrome and a raw edit URL.

    Stripped here, at the HTML stage, rather than pattern-matched back out
    of the Markdown later — Docling never sees it, so nothing downstream
    needs to know this was ever there.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_fragment, "html.parser")
    for span in soup.find_all("span", class_="mw-editsection"):
        span.decompose()
    return str(soup)


def _strip_toc(html_fragment: str) -> str:
    """Remove MediaWiki's table of contents box from parsed page HTML.

    The "Contents" box lists the page's own headings; parsed, it becomes a
    section of its own that matches every query about the page and says
    nothing. The headings themselves stay where they are.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_fragment, "html.parser")
    for toc in soup.select("#toc, .toc, #mw-toc-heading"):
        toc.decompose()
    return str(soup)


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


# -- syncing: the wiki's current pages against what the knowledge base holds --
#
# A wiki page changes in place, unlike a DocDB version. The knowledge base
# keeps one current copy per page: the raw document is the page's lasting
# identity (matched on MediaWiki's pageid, which survives renames), so its
# visibility decisions and redactions stay; what was parsed from it is
# rebuilt when the revision changes. Deleted pages are removed -- unless the
# page list looks broken, see plan_sync.

#: Deleting more than this fraction of the known pages in one run means the
#: page list is more likely broken (an expired login, an API error, a paging
#: bug) than the wiki emptied; the run then deletes nothing and says so.
MAX_DELETE_FRACTION = 0.1


def plan_sync(remote: Optional[List[Dict[str, Any]]], local: List[Dict[str, Any]], full: bool = False,
              max_delete_fraction: float = MAX_DELETE_FRACTION) -> Dict[str, Any]:
    """What a sync would do. Pure: no network, no database.

    remote: the wiki's pages, each {"page_id", "title", "revision_id"}; None
        when the listing failed or was incomplete.
    local: the source's raw documents, each {"raw_id", "page_id", "doc_id",
        "revision_id"}.
    full: re-import every page, changed or not (a template edit changes
        pages without a new revision of their own).

    Returns {"new", "changed", "unchanged", "deleted"} (lists; changed and
    unchanged entries carry the local raw_id; a renamed page is "changed"),
    "deletions_blocked" (a reason, or None) and "remote_count".
    """
    plan: Dict[str, Any] = {"new": [], "changed": [], "unchanged": [], "deleted": [],
                            "deletions_blocked": None, "remote_count": None}
    by_page = {str(r["page_id"]): r for r in local if r.get("page_id") is not None}
    if remote is None:
        plan["deletions_blocked"] = "the page list could not be read completely"
        return plan
    plan["remote_count"] = len(remote)
    seen = set()
    for page in remote:
        key = str(page["page_id"])
        seen.add(key)
        mine = by_page.get(key)
        if mine is None:
            plan["new"].append(page)
            continue
        entry = {**page, "raw_id": mine["raw_id"], "old_doc_id": mine.get("doc_id")}
        renamed = mine.get("doc_id") != page_doc_id(page["title"])
        if full or renamed or str(mine.get("revision_id")) != str(page.get("revision_id")):
            plan["changed"].append(entry)
        else:
            plan["unchanged"].append(entry)
    gone = [r for key, r in by_page.items() if key not in seen]
    # Raws without a page_id (an old import) cannot be matched: left alone.
    if not remote:
        plan["deletions_blocked"] = "the wiki returned no pages"
    elif by_page and len(gone) > max(1, max_delete_fraction * len(by_page)):
        plan["deletions_blocked"] = (f"{len(gone)} of {len(by_page)} known pages would go "
                                     f"(limit {max_delete_fraction:.0%}): the page list looks wrong")
    if plan["deletions_blocked"] is None:
        plan["deleted"] = gone
    return plan


def page_doc_id(title: str) -> str:
    """A page's doc_id: its title as in the wiki URL (spaces as underscores)."""
    return title.replace(" ", "_")


def section_anchors(sections: List[Dict[str, Any]]) -> Dict[str, str]:
    """{heading text: anchor} from action=parse's sections (headings may carry HTML)."""
    out: Dict[str, str] = {}
    for sec in sections or []:
        line = re.sub(r"<[^>]+>", "", sec.get("line") or "").strip()
        if line and sec.get("anchor") and line not in out:
            out[line] = sec["anchor"]
    return out


def page_url(wiki_url: str, doc_id: str) -> str:
    """The page's address. Characters a path cannot carry (? # & % ...) are
    percent-encoded; the ones MediaWiki titles use as they are stay readable."""
    from urllib.parse import quote

    return f"{wiki_url}/wiki/{quote(doc_id, safe='/:()_-.,+!*~$@;')}"


_THUMB = re.compile(r"^(?P<base>.*/images)/thumb/(?P<path>[0-9a-f]/[0-9a-f]{2}/[^/]+)/[^/]+$")


def original_image_url(src: str) -> str:
    """The full-size file behind a MediaWiki thumbnail URL (other URLs unchanged).

    /images/thumb/a/ab/Plot.png/300px-Plot.png -> /images/a/ab/Plot.png
    """
    m = _THUMB.match(src)
    return f"{m.group('base')}/{m.group('path')}" if m else src


#: Images larger than this are left as links: a page's HTML carries them
#: inline, and no figure worth describing needs more.
MAX_INLINE_IMAGE_BYTES = 25 * 1024 * 1024

_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
                "webp": "image/webp", "svg": "image/svg+xml"}


def inline_images(html: str, wiki_url: str, fetch) -> tuple:
    """Put the wiki's own images into the HTML as data: URIs, so the parser sees their pixels.

    Docling's HTML backend loads data: images only (remote fetches stay off:
    the HTML is untrusted input), so each <img> of this wiki is downloaded
    with `fetch(url) -> bytes | None` -- the importer's authenticated session
    -- at full size rather than as the thumbnail, and embedded. Images of
    other hosts, failed downloads and oversized files keep their link.
    Returns (html, embedded count).
    """
    import base64
    from urllib.parse import urljoin, urlparse

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(wiki_url).netloc
    embedded = 0
    cache: Dict[str, Optional[str]] = {}
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        url = original_image_url(urljoin(wiki_url + "/", src))
        if urlparse(url).netloc != host:
            continue
        if url not in cache:
            data = fetch(url)
            ext = url.rsplit(".", 1)[-1].lower()
            mime = _IMAGE_TYPES.get(ext)
            cache[url] = (f"data:{mime};base64,{base64.b64encode(data).decode()}"
                          if data and mime and len(data) <= MAX_INLINE_IMAGE_BYTES else None)
        if cache[url]:
            img["src"] = cache[url]
            for attr in ("srcset", "width", "height"):
                img.attrs.pop(attr, None)
            embedded += 1
    return str(soup), embedded


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
        auth: Optional[str] = None,
        namespaces: tuple = (0,),
    ):
        """Initialize the MediaWiki source.

        Args:
            wiki_url: Base URL of the wiki (e.g., "https://mu2ewiki.fnal.gov")
            source_id: Source identifier for knowledge base
            delay: Delay between requests in seconds
            timeout: Request timeout in seconds
            skip_existing: If True, skip pages already in the database
            use_kerberos: If True, use Kerberos auth via curl --negotiate
                (the default auth; ignored when `auth` is given)
            auth: "kerberos" (curl --negotiate), "sso" (Fermilab SSO with
                MU2E_DOCDB_USERNAME/PASSWORD -- the internal wiki, which
                does not take Kerberos on its API) or "none" (public wiki)
            namespaces: Namespace ids a sync lists (0: main)
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
        self.auth = auth or ("kerberos" if use_kerberos else "none")
        if self.auth not in ("kerberos", "sso", "none"):
            raise ValueError(f"auth must be kerberos, sso or none, not {self.auth!r}")
        self.use_kerberos = self.auth == "kerberos"
        self.namespaces = tuple(int(n) for n in namespaces)
        self._session = None   # requests.Session, for auth="sso"

    # Wiki pages have strong section structure: one chunk per section.
    chunk_strategy = "section"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._session is not None:
            self._session.close()
            self._session = None

    def _sso(self):
        """The logged-in session (auth="sso"), created on first use."""
        if self._session is None:
            from .fnal_sso import sso_session
            self._session = sso_session(f"{self.wiki_url}/wiki/Special:UserLogin", timeout=self.timeout)
            logger.info(f"Logged in to {self.wiki_url} through Fermilab SSO")
        return self._session

    def _fetch_bytes(self, url: str) -> Optional[bytes]:
        """A file of this wiki (an image), with the importer's credentials; None on failure."""
        try:
            if self.auth == "sso":
                r = self._sso().get(url, timeout=self.timeout)
                return r.content if r.ok else None
            cmd = ["curl", "-s", "-f", "--max-time", str(int(self.timeout)), "-A", _BROWSER_UA]
            if self.auth == "kerberos":
                cmd.extend(["--negotiate", "-u", ":"])
            result = subprocess.run(cmd + [url], capture_output=True, timeout=self.timeout + 5)
            return result.stdout if result.returncode == 0 and result.stdout else None
        except Exception as e:  # noqa: BLE001 - an image that cannot be fetched keeps its link
            logger.warning(f"Could not fetch {url}: {e}")
            return None

    def _api_request(self, params: Dict[str, str]) -> Optional[Dict]:
        """Make a MediaWiki API request using curl.

        Uses curl --negotiate for Kerberos authentication.

        Args:
            params: Query parameters for the API request

        Returns:
            JSON response as dict, or None if request failed
        """
        params["format"] = "json"

        if self.auth == "sso":
            try:
                r = self._sso().get(self.api_url, params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001 - same contract as the curl path: None on failure
                logger.error(f"API request failed ({params.get('action')}): {e}")
                return None

        # Build URL with query params. urlencode() percent-escapes the values,
        # which matters for page titles containing &, =, #, + or spaces - those
        # would otherwise corrupt the query string.
        query_string = urlencode(params)
        url = f"{self.api_url}?{query_string}"

        # -A: mu2ewiki.fnal.gov sits behind Cloudflare, which silently drops
        # (never responds — no error, no challenge page) requests carrying
        # curl's default "curl/x.y.z" User-Agent. A browser-shaped UA gets
        # answered normally.
        cmd = ["curl", "-s", "--max-time", str(int(self.timeout)), "-A", _BROWSER_UA]
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
                "prop": "text|categories|links|sections|displaytitle|revid",
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
        revision_id = parse_data.get("revid")
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
            "revision_id": revision_id,
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

        page = self._build_page(title, output_dir)
        if page is None:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"Failed to fetch content for page '{title}'",
            }

        # Add to knowledge base
        result = add_document(
            page["html_path"],
            source_id=self.source_id,
            doc_id=page["doc_id"],
            uri=page["uri"],
            meta=page["meta"],
            creating_time=page["created"],
            update_time=page["updated"],
            copy_to_kb=True,
            session=session,
        )

        result["error"] = None
        logger.info(
            f"Added wiki page '{title}': {result.get('num_documents', 0)} document(s)"
        )
        return result

    # -- building one page -----------------------------------------------------

    def _build_page(self, title: str, output_dir: Path) -> Optional[Dict[str, Any]]:
        """Fetch one page and write it as HTML for the parser, images inlined.

        Returns {"html_path", "doc_id", "uri", "meta", "created", "updated"},
        or None if the page could not be fetched.
        """
        logger.info(f"Fetching wiki page: {title}")
        content = self._get_page_content(title)
        if not content or not content.get("html"):
            return None

        body_html = _strip_toc(_strip_edit_section_links(content["html"]))
        body_html, images = inline_images(body_html, self.wiki_url, self._fetch_bytes)
        # The title once, as the top heading: it then roots every section
        # path ("TrackerIntro > Straws"); a <title> as well would repeat it.
        # The page's identity in the file: raw documents are deduplicated by
        # content hash, and two pages (or the same page in both wikis) can
        # render identical HTML -- this keeps each page's file its own.
        html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="kb-page" content="{self.wiki_url} {content.get('page_id')}"></head>
<body>
<h1>{content['title']}</h1>
{body_html}
</body>
</html>"""
        doc_id = page_doc_id(title)
        safe_filename = doc_id.replace("/", "_").replace("\\", "_")
        html_path = Path(output_dir) / f"{safe_filename}.html"
        html_path.write_text(html, encoding="utf-8")

        meta = {
            "title": content["title"],
            "page_id": content.get("page_id"),
            "revision_id": content.get("revision_id"),
            "categories": content.get("categories", []),
            "sections": [s.get("line", "") for s in content.get("sections", [])],
            # heading -> its anchor on the page, so a hit can link to the
            # section it came from (see the SECTION_LINKS line of a hit)
            "section_anchors": section_anchors(content.get("sections", [])),
            "wiki_url": self.wiki_url,
            "inlined_images": images,
        }
        # Revision history, promoted to the Document columns so search and
        # the web UI can filter on when the page was written and last edited
        # rather than when we happened to scrape it.
        times = self._get_revision_times(title)
        if times["created"]:
            meta["wiki_created"] = times["created"].isoformat()
        if times["updated"]:
            meta["wiki_last_edit"] = times["updated"].isoformat()
        return {"html_path": html_path, "doc_id": doc_id, "uri": page_url(self.wiki_url, doc_id),
                "meta": meta, "created": times["created"], "updated": times["updated"]}

    # -- sync --------------------------------------------------------------------

    def list_pages(self) -> Optional[List[Dict[str, Any]]]:
        """Every content page of self.namespaces with its current revision; None if incomplete.

        One generator query per 500 pages (allpages + prop=info gives each
        page's lastrevid); redirects are left out. Any failed request makes
        the whole list None: a partial list would look like deleted pages.
        """
        pages: List[Dict[str, Any]] = []
        for ns in self.namespaces:
            params = {"action": "query", "generator": "allpages", "gapnamespace": str(ns),
                      "gapfilterredir": "nonredirects", "gaplimit": "500", "prop": "info"}
            while True:
                data = self._api_request(dict(params))
                if not data or "error" in data:
                    logger.error(f"Listing namespace {ns} failed: {(data or {}).get('error', 'no response')}")
                    return None
                for page in ((data.get("query") or {}).get("pages") or {}).values():
                    if "missing" in page or "invalid" in page:
                        continue
                    pages.append({"page_id": page["pageid"], "title": page["title"],
                                  "revision_id": page.get("lastrevid"), "namespace": ns})
                cont = data.get("continue")
                if not cont:
                    break
                params.update(cont)
                time.sleep(self.delay)
        logger.info(f"{self.wiki_url}: {len(pages)} page(s) in namespace(s) {list(self.namespaces)}")
        return pages

    def _local_pages(self) -> List[Dict[str, Any]]:
        """This source's raw documents, as plan_sync's `local`."""
        from ..kb import get_db_session
        from ..kb.db_models import RawDocument

        with get_db_session() as session:
            rows = session.query(RawDocument.id, RawDocument.doc_id, RawDocument.meta).filter(
                RawDocument.source_id == self.source_id).all()
        return [{"raw_id": rid, "doc_id": doc_id, "page_id": (meta or {}).get("page_id"),
                 "revision_id": (meta or {}).get("revision_id")} for rid, doc_id, meta in rows]

    def sync(self, apply: bool = False, full: bool = False, limit: Optional[int] = None,
             max_delete_fraction: float = MAX_DELETE_FRACTION, auto_embed: bool = True,
             auto_summarize: bool = True, progress=None) -> Dict[str, Any]:
        """Bring the knowledge base in line with the wiki (dry run unless apply).

        New pages are added; a page whose revision changed (every page with
        `full`) is re-imported into its existing raw document; pages gone
        from the wiki are removed -- never when the page list looks broken
        (plan_sync). Then summaries and section chunks for what is new.
        Returns the plan with counts of what was done.
        """
        plan = plan_sync(self.list_pages(), self._local_pages(), full=full,
                         max_delete_fraction=max_delete_fraction)
        todo_new, todo_changed = plan["new"], plan["changed"]
        if limit:
            todo_new, todo_changed = todo_new[:limit], todo_changed[:max(0, limit - len(todo_new))]
        result = {"plan": plan, "added": 0, "updated": 0, "removed": 0, "failed": [], "applied": apply}
        if not apply:
            return result
        if todo_new or todo_changed:
            # A page is re-imported only when it changes: one imported with
            # placeholder descriptions (an expired ALCF token) would keep them.
            from ..parser.image_descriptions import vision_endpoint_problem
            problem = vision_endpoint_problem()
            if problem:
                raise RuntimeError(f"Image descriptions would fail, nothing imported: {problem}")
            self._ensure_source()   # a new source (mu2e-internal-wiki) has no row yet

        out_dir = Path(tempfile.mkdtemp(prefix=f"kb_wiki_{self.source_id}_"))
        for page in todo_new:
            try:
                self._add_page(page, out_dir)
                result["added"] += 1
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the sync
                logger.error(f"Adding '{page['title']}' failed: {e}")
                result["failed"].append((page["title"], str(e)))
            if progress:
                progress("added", page)
        for page in todo_changed:
            try:
                self._update_page(page, out_dir)
                result["updated"] += 1
            except Exception as e:  # noqa: BLE001
                logger.error(f"Updating '{page['title']}' failed: {e}")
                result["failed"].append((page["title"], str(e)))
            if progress:
                progress("updated", page)
        for gone in plan["deleted"]:
            from ..kb.documents.operations import delete_raw_document
            delete_raw_document(gone["raw_id"], delete_linked_documents=True)
            logger.info(f"Removed '{gone['doc_id']}' (page {gone['page_id']}): no longer in the wiki")
            result["removed"] += 1
            if progress:
                progress("removed", gone)

        if auto_summarize and (result["added"] or result["updated"]):
            from ..kb.tools import summarize_all
            summarize_all(source_id=self.source_id)
        if auto_embed and (result["added"] or result["updated"]):
            from ..kb.tools import chunk_and_embed_all
            chunk_and_embed_all(source_id=self.source_id, chunk_strategy=self.chunk_strategy)
        return result

    def _stored_path(self, page_id) -> Path:
        """Where a page's file is kept: by page id, which is unique and survives
        renames (titles are not injective as file names: A/B and A-B)."""
        from ..config import get_data_dir

        return Path(get_data_dir()) / "sources" / self.source_id / f"{self.source_id}-page-{page_id}.html"

    def _store(self, built: Dict[str, Any], page_id) -> Path:
        import shutil

        from ..secure_file import ensure_private_dir

        dest = self._stored_path(page_id)
        ensure_private_dir(dest.parent)
        shutil.copyfile(built["html_path"], dest)
        return dest

    def _add_page(self, page: Dict[str, Any], out_dir: Path) -> None:
        from ..kb import get_db_session
        from ..kb.db_models import RawDocument

        built = self._build_page(page["title"], out_dir)
        if built is None:
            raise RuntimeError("could not fetch the page")
        path = self._store(built, page["page_id"])
        result = add_document(path, source_id=self.source_id, doc_id=built["doc_id"], uri=built["uri"],
                              meta=built["meta"], creating_time=built["created"], update_time=built["updated"])
        with get_db_session() as session:
            raw = session.get(RawDocument, result.get("raw_document_id")) if result.get("raw_document_id") else None
            if raw is None or raw.source_id != self.source_id or raw.doc_id != built["doc_id"]:
                raise RuntimeError(f"landed in raw document {result.get('raw_document_id')} "
                                   f"({getattr(raw, 'source_id', None)}/{getattr(raw, 'doc_id', None)}), not its own")
        logger.info(f"Added '{page['title']}' (page {page['page_id']})")

    def _update_page(self, page: Dict[str, Any], out_dir: Path) -> None:
        """Re-import a changed (or renamed) page into its existing raw document.

        The raw document keeps its id, so its visibility decisions and
        redactions stay; its file, hash, doc_id, URI and metadata are
        updated, everything parsed from the old content is deleted, and
        add_document -- which finds the raw document by the new hash --
        parses the new content. An automatic visibility goes back on the
        privacy hold until the new content is scanned (hold_changed_document).

        The new revision id is recorded only once the new content is parsed:
        if parsing fails, the raw document keeps the old one, so the next sync
        sees the page as changed and tries again (its old documents are gone
        by then; the page is missing from search until that retry succeeds).
        """
        import hashlib

        from ..config import get_data_dir
        from ..kb import get_db_session
        from ..kb.db_models import RawDocument
        from ..kb.documents.operations import delete_derived_documents, hold_changed_document

        built = self._build_page(page["title"], out_dir)
        if built is None:
            raise RuntimeError("could not fetch the page")
        dest = self._store(built, page["page_id"])
        data = dest.read_bytes()

        with get_db_session() as session:
            raw = session.get(RawDocument, page["raw_id"])
            if raw is None:
                raise RuntimeError(f"raw document {page['raw_id']} is gone")
            old_path = raw.file_path
            old_revision = (raw.meta or {}).get("revision_id")
            raw.doc_id = built["doc_id"]
            raw.uri = built["uri"]
            raw.file_path = str(dest.absolute())
            raw.content_hash = hashlib.sha256(data).hexdigest()
            raw.file_size = len(data)
            raw.meta = {**(raw.meta or {}), **built["meta"], "filename": dest.name, "revision_id": old_revision}
            deleted = delete_derived_documents(raw.id, session=session)
            held = hold_changed_document(raw.id, session)
            session.commit()

        result = add_document(dest, source_id=self.source_id, doc_id=built["doc_id"],
                              uri=built["uri"], meta=built["meta"], creating_time=built["created"],
                              update_time=built["updated"])
        if result.get("raw_document_id") != page["raw_id"]:
            raise RuntimeError(f"parsed into raw document {result.get('raw_document_id')}, "
                               f"not the page's {page['raw_id']} (same content as another raw document?)")
        with get_db_session() as session:     # parsed: now the revision is current
            raw = session.get(RawDocument, page["raw_id"])
            raw.meta = {**(raw.meta or {}), "revision_id": built["meta"].get("revision_id")}
            session.commit()
        if old_path and old_path != str(dest.absolute()):
            # A renamed page: its old file is ours (data/sources/<source>), and no longer used.
            old = Path(old_path)
            if old.is_file() and Path(get_data_dir()).resolve() in old.resolve().parents:
                old.unlink()
        logger.info(f"Updated '{page['title']}' (page {page['page_id']}, revision {page.get('revision_id')}): "
                    f"{deleted} old document(s) replaced{', held for the privacy scan' if held else ''}")
