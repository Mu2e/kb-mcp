#!/usr/bin/env python3
"""Mu2e DocDB importer for fetching documents and metadata via FNAL SSO."""

import io
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, quote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from .base import Source
from ..kb import add_document

logger = logging.getLogger(__name__)

# DocDB renders its timestamps in Fermilab local time with no zone marker,
# e.g. "10 Apr 2026, 16:28".
_DOCDB_TZ = ZoneInfo("America/Chicago")
_DOCDB_TIME_FORMAT = "%d %b %Y, %H:%M"


def parse_docdb_datetime(value: Optional[str]) -> Optional[datetime]:
    """Convert a DocDB timestamp string to an aware UTC datetime.

    DocDB writes "Document Created" / "Contents Revised" as Fermilab local
    time without a zone (e.g. "10 Apr 2026, 16:28"), so the string is
    anchored to America/Chicago and converted to UTC for storage.

    Returns None for missing or unparseable values — the timestamps are
    nice-to-have metadata and must never fail an import.
    """
    if not value:
        return None
    try:
        naive = datetime.strptime(value.strip(), _DOCDB_TIME_FORMAT)
    except ValueError:
        logger.warning(f"Could not parse DocDB timestamp {value!r}")
        return None
    return naive.replace(tzinfo=_DOCDB_TZ).astimezone(timezone.utc)


class DocDBSource(Source):
    """Importer for Mu2e DocDB (or any FNAL DocDB instance).

    Authenticates via FNAL SSO using MU2E_DOCDB_USERNAME and
    MU2E_DOCDB_PASSWORD environment variables, then fetches documents
    and ingests them into the knowledge base.
    """

    def __init__(
        self,
        base_url: str = "https://mu2e-docdb.fnal.gov/cgi-bin/sso/",
        source_id: str = "mu2e-docdb",
        delay: float = 0.5,
        timeout: float = 30.0,
        skip_existing: bool = False,
        force_reparse: bool = False,
        skip_parse: bool = False,
        login: bool = True,
    ):
        """Initialize the DocDB source.

        Args:
            base_url: Base URL of the DocDB SSO endpoint.
            source_id: Source identifier for the knowledge base.
            delay: Delay between requests in seconds.
            timeout: Request timeout in seconds.
            skip_existing: If True, skip documents already in the database.
            skip_parse: If True, only download files and register RawDocument
                rows — no Docling/parsing, no chunking, no embedding. Lets a
                large backfill be staged (network- and rate-limit-bound)
                separately from parsing it (CPU-bound); parse the staged
                rows later with `kb reparse --from-raw`.
            login: If True, authenticate on construction using env vars.
        """
        super().__init__(
            source_id=source_id,
            name="Mu2e DocDB",
            description="Mu2e Document Database (FNAL DocDB)",
            base_uri=base_url,
            delay=delay,
            timeout=timeout,
            meta={"scraper": "docdb.py"},
        )
        self.base_url = base_url.rstrip("/") + "/"
        self.skip_existing = skip_existing
        self.force_reparse = force_reparse
        self.skip_parse = skip_parse
        self.session: Optional[requests.Session] = None

        if login:
            missing = [
                v for v in ("MU2E_DOCDB_USERNAME", "MU2E_DOCDB_PASSWORD")
                if not os.getenv(v)
            ]
            if missing:
                raise ValueError(
                    f"Missing required environment variables: {', '.join(missing)}. "
                    "Please set these before running the DocDB importer."
                )
            self._login()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            self.session.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self):
        """Authenticate to DocDB via FNAL SSO (username/password flow)."""
        session = requests.Session()

        # Step 1: initial request — redirects to PingFed
        response = session.get(self.base_url, timeout=self.timeout)

        # Step 2: choose username/password authentication method
        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.find("form")
        auth_url = urljoin("https://pingprod.fnal.gov", form["action"])
        response = session.get(
            auth_url,
            params={"pfidpadapterid": "ad..FormBased", "rememberChoice": "false"},
            timeout=self.timeout,
        )

        # Step 3: submit credentials
        soup = BeautifulSoup(response.text, "html.parser")
        login_form = soup.find("form")
        if not login_form:
            session.close()
            raise RuntimeError("Could not find login form on DocDB SSO page")

        login_url = urljoin("https://pingprod.fnal.gov", login_form["action"])
        login_data = {
            "pf.username": os.getenv("MU2E_DOCDB_USERNAME"),
            "pf.pass": os.getenv("MU2E_DOCDB_PASSWORD"),
            "pf.ok": "clicked",
            "pf.adapterId": "FormBased",
            "pf.cancel": "",
        }
        response = session.post(login_url, data=login_data, timeout=self.timeout)

        # Step 4: forward SAML assertion if present
        soup = BeautifulSoup(response.text, "html.parser")
        saml_form = soup.find("form")
        if saml_form:
            saml_url = saml_form["action"]
            saml_response_input = saml_form.find("input", {"name": "SAMLResponse"})
            relay_state_input = saml_form.find("input", {"name": "RelayState"})
            if saml_response_input and relay_state_input:
                session.post(
                    saml_url,
                    data={
                        "RelayState": relay_state_input["value"],
                        "SAMLResponse": saml_response_input["value"],
                    },
                    timeout=self.timeout,
                )
        else:
            logger.warning("No SAML form found — login may have failed")

        self.session = session
        logger.info("DocDB login successful")

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _check_response(self, response: requests.Response):
        if not response.ok:
            raise RuntimeError(
                f"Request to {response.url} failed with status {response.status_code}"
            )
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.title.string if soup.title else None
        if title and title == "Select Authentication System":
            raise RuntimeError(
                f"Session expired or login required. "
                f"Set MU2E_DOCDB_USERNAME / MU2E_DOCDB_PASSWORD and retry."
            )
        # Ping SSO's "speed bump" page for a concurrent sign-on under the
        # same identity (e.g. an active browser tab or another script
        # logging in at the same time). It 200s with an HTML page that has
        # none of DocDB's expected content, so _parse_list()/get_meta()
        # would otherwise silently see "no documents" / "no metadata"
        # instead of a clear auth failure.
        if title and title == "Multiple Sign-On Delay":
            raise RuntimeError(
                "DocDB SSO returned a 'Multiple Sign-On Delay' page — "
                "another sign-on for this identity is in progress "
                "(e.g. a browser tab or another concurrent session). "
                "Close it and retry."
            )

    def _get_html(self, doc_id: int) -> str:
        url = f"{self.base_url}ShowDocument?docid={doc_id}"
        response = self.session.get(url, timeout=self.timeout)
        self._check_response(response)
        return response.text

    # ------------------------------------------------------------------
    # Metadata parsing
    # ------------------------------------------------------------------

    def get_meta(self, doc_id: int) -> Optional[Dict]:
        """Fetch and parse the metadata page for a single document.

        Returns None if the document does not exist or is not accessible.
        """
        html = self._get_html(doc_id)
        soup = BeautifulSoup(html, "html.parser")
        page_title = soup.title.string if soup.title else None
        if not page_title or page_title == f"Mu2e-doc-{doc_id}-v: Not authorized":
            return None

        result: Dict[str, Any] = {}

        # Left-side statistics
        for field, label in {
            "docid_str": "Document #:",
            "type": "Document type:",
            "created": "Document Created:",
            "revised_content": "Contents Revised:",
            "revised_meta": "Metadata Revised:",
        }.items():
            key = soup.find("dt", string=label)
            if key:
                tag = key.find_next("dd")
                if tag:
                    result[field] = tag.text.strip()

        # Title
        doc_title_div = soup.find("div", id="DocTitle")
        if doc_title_div:
            h1 = doc_title_div.find("h1")
            if h1:
                result["title"] = h1.text.strip()

        # InfoHeader fields
        info_fields = {
            "abstract": {"search": "Abstract:", "list": False},
            "files":    {"search": "Files in Document:", "list": True},
            "topics":   {"search": "Topics:", "list": True},
            "authors":  {"search": "Authors:", "list": True},
            "keyword":  {"search": "Keywords:", "list": True},
        }
        for field, cfg in info_fields.items():
            key = soup.find("dt", class_="InfoHeader", string=cfg["search"])
            if not key:
                continue
            if field in ("topics", "authors"):
                tag = key.find_next("ul")
            else:
                tag = key.find_next("dd")
            if not tag:
                continue
            if not cfg["list"]:
                result[field] = tag.text
            elif field == "files":
                result[field] = [
                    {
                        "link": item.find("a").get("href"),
                        "filename": item.find("a").get("title"),
                        "text": re.sub(r"\s*\(.*\)\s*$", "", item.text),
                    }
                    for item in tag.find_all("li")
                    if item.find("a")
                ]
            else:
                result[field] = [item.text for item in tag.find_all("a")]

        # Events (Associated with Events)
        event_div = soup.find("div", id="EventInfo")
        if event_div:
            events = []
            for dd in event_div.find_all("dd"):
                a = dd.find("a", class_="Event")
                if a:
                    event = {"name": a.text.strip(), "link": a.get("href", "")}
                    rest = re.sub(re.escape(a.text.strip()), "", dd.get_text(separator=" ")).strip()
                    date_m = re.search(r"held on\s+(.+?)\s+in\s+(.+)", rest)
                    if date_m:
                        event["date"] = date_m.group(1).strip()
                        event["location"] = date_m.group(2).strip()
                    events.append(event)
            if events:
                result["events"] = events

        if "docid_str" not in result:
            return None

        parts = result["docid_str"].split("-")
        result["docid"] = int(parts[2])
        result["version"] = int(parts[3][1:])
        return result

    # ------------------------------------------------------------------
    # File retrieval
    # ------------------------------------------------------------------

    def _get_file(self, url: str) -> Optional[Dict]:
        """Download a single file from a DocDB URL.

        Returns dict with keys 'type' and 'document' (io.BytesIO), or None.
        """
        response = self.session.get(url, stream=True, timeout=self.timeout)
        self._check_response(response)
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            logger.warning(f"Got HTML instead of file from {url} — session may have expired")
            return None
        file_type = content_type.split("/")[-1].split(";")[0].strip()
        return {"type": file_type, "document": io.BytesIO(response.content)}

    # ------------------------------------------------------------------
    # Listing / searching
    # ------------------------------------------------------------------

    def list_latest(self, days: int = 30) -> List[Dict]:
        """Return a list of recently updated documents."""
        url = f"{self.base_url}ListBy?days={days}"
        response = self.session.get(url, timeout=self.timeout)
        self._check_response(response)
        return self._parse_list(response.text)

    def search(
        self,
        text: Optional[str] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
    ) -> List[Dict]:
        """Search DocDB by title/abstract/keywords and optional date range."""
        data: Dict[str, str] = {
            "outerlogic": "AND",
            "innerlogic": "OR",
            "mode": "date",
            "titlesearchmode": "allword",
            "abstractsearchmode": "allword",
            "keywordsearchmode": "allword",
            "revisionnotesearchmode": "allword",
            "pubinfosearchmode": "allword",
            "filesearchmode": "allword",
            "filedescsearchmode": "allword",
            "includesubtopics": "on",
        }
        if text:
            data["titlesearch"] = text
            data["abstractsearch"] = text
            data["keywordsearch"] = text
        for key_prefix, dt in (("before", before), ("after", after)):
            if dt:
                data[f"{key_prefix}day"] = str(dt.day)
                data[f"{key_prefix}month"] = dt.strftime("%b")
                data[f"{key_prefix}year"] = str(dt.year)
            else:
                data[f"{key_prefix}day"] = "--"
                data[f"{key_prefix}month"] = "---"
                data[f"{key_prefix}year"] = "----"

        response = self.session.post(
            self.base_url + "Search", data=data, timeout=self.timeout
        )
        self._check_response(response)
        return self._parse_list(response.text)

    def _parse_list(self, html: str) -> List[Dict]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", {"id": "DocumentTable"})
        if not table:
            return []
        documents = []
        for row in table.find("tbody").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            doc_id = cells[0].find("a").text.split("-")[0].strip()
            title = cells[1].find("a").text.strip()
            link = cells[1].find("a").get("href")
            authors = [a.text.strip() for a in cells[2].find_all("a")]
            if cells[2].find("i"):
                authors.append("et al.")
            topics = [a.text.strip() for a in cells[3].find_all("a")]
            date_str = cells[4].text.strip()
            try:
                last_updated = datetime.strptime(date_str, "%d %b %Y")
            except ValueError:
                last_updated = date_str
            documents.append(
                {
                    "id": doc_id,
                    "title": title,
                    "authors": authors,
                    "topics": topics,
                    "last_updated": last_updated,
                    "link": link,
                }
            )
        return documents

    # ------------------------------------------------------------------
    # Source interface
    # ------------------------------------------------------------------

    def fetch_items(
        self,
        query: Optional[str] = None,
        max_results: Optional[int] = None,
        days: Optional[int] = None,
        doc_ids: Optional[List[int]] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
    ) -> List[Dict]:
        """Fetch document listings from DocDB.

        Args:
            query: Text to search in title/abstract/keywords.
            max_results: Maximum number of documents to return.
            days: If set (and no query/doc_ids), list documents updated in the
                  last N days. Defaults to 30 when neither query nor doc_ids
                  are provided.
            doc_ids: Explicit list of DocDB IDs to fetch (skips listing step).
            before: Upper date bound for search.
            after: Lower date bound for search.

        Returns:
            List of item dicts, each with at least an 'id' key.
        """
        if doc_ids:
            items = [{"id": str(d)} for d in doc_ids]
        elif query or before or after:
            items = self.search(text=query, before=before, after=after)
        else:
            items = self.list_latest(days=days or 30)

        if max_results:
            items = items[:max_results]

        logger.info(f"Found {len(items)} item(s) to process")
        return items

    def process_item(
        self,
        item: Dict[str, Any],
        output_dir: Path,
        session: Any,
    ) -> Dict[str, Any]:
        """Fetch, parse, and ingest a single DocDB document.

        Args:
            item: Dict with at least an 'id' key (DocDB document number).
            output_dir: Directory for temporary file storage.
            session: Database session.

        Returns:
            Standard result dict with document_ids, num_documents, parsed, error.
        """
        doc_id_str = str(item["id"])

        # Check for existing document before fetching — match any file from this doc
        if self.skip_existing:
            from ..kb.db_models import RawDocument
            existing = session.query(RawDocument).filter(
                RawDocument.source_id == self.source_id,
                RawDocument.doc_id.like(f"{doc_id_str}-%"),
            ).first()
            if existing:
                logger.info(f"Skipping doc {doc_id_str} — already in database")
                return {
                    "document_ids": [],
                    "num_documents": 0,
                    "parsed": False,
                    "raw_document_id": existing.id,
                    "skipped": True,
                    "error": None,
                }

        # Fetch metadata
        meta = self.get_meta(int(doc_id_str))
        if meta is None:
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"Document {doc_id_str} not found or not accessible",
            }

        if not meta.get("files"):
            return {
                "document_ids": [],
                "num_documents": 0,
                "parsed": False,
                "error": f"No files attached to document {doc_id_str}",
            }

        # Build metadata dict for the knowledge base — shared across all files
        kb_meta: Dict[str, Any] = {
            "title": meta.get("title", ""),
            "abstract": meta.get("abstract", ""),
            "authors": meta.get("authors", []),
            "topics": meta.get("topics", []),
            "keywords": meta.get("keyword", []),
            "docdb_id": meta["docid"],
            "version": meta.get("version"),
            "created": meta.get("created"),
            "revised_content": meta.get("revised_content"),
        }
        if meta.get("events"):
            kb_meta["events"] = meta["events"]

        # DocDB's own timestamps, promoted to the Document columns so search
        # and the web UI can filter on when the document was written rather
        # than when we happened to ingest it. update_time tracks "Contents
        # Revised" only: "Metadata Revised" moves when someone retags a
        # topic or author, which does not change anything we parsed.
        creating_time = parse_docdb_datetime(meta.get("created"))
        update_time = parse_docdb_datetime(meta.get("revised_content"))

        document_ids: List[str] = []
        raw_document_ids: List[str] = []

        for file_info in meta["files"]:
            filename = file_info.get("filename") or file_info.get("text", "unknown")
            file_link = file_info.get("link")
            if not file_link:
                logger.warning(f"No link for file '{filename}' in doc {doc_id_str}")
                continue

            # Skip unsupported file types before downloading
            from ..parser.utils import PARSER_MAP
            file_ext = Path(filename).suffix.lstrip(".").lower()
            if file_ext not in PARSER_MAP:
                logger.info(f"Skipping unsupported file type '{file_ext}': {filename}")
                continue

            logger.debug(f"Downloading {filename} from doc {doc_id_str}")
            file_data = self._get_file(file_link)
            if file_data is None:
                logger.warning(f"Could not download {filename} from doc {doc_id_str}")
                continue

            # Write to a temp file so add_document can process it
            safe_name = re.sub(r"[^\w.\-]", "_", filename)
            tmp_path = output_dir / f"docdb-{doc_id_str}-{safe_name}"
            tmp_path.write_bytes(file_data["document"].getvalue())

            # Build per-file doc_id. copy_to_kb produces {source_id}-{doc_id}{ext},
            # so use the stem as doc_id for a clean filename. If multiple files share
            # the same stem (e.g. MB-374.pdf and MB-374.pptx), append _{ext} to
            # disambiguate: "56359-MB-374_pptx" → mu2e-docdb-56359-MB-374_pptx.pptx
            safe_stem = Path(safe_name).stem
            safe_ext = Path(safe_name).suffix.lstrip(".")
            all_stems = [Path(re.sub(r"[^\w.\-]", "_", f.get("filename") or "")).stem
                         for f in meta["files"] if f.get("filename")]
            if all_stems.count(safe_stem) > 1:
                file_doc_id = f"{doc_id_str}-{safe_stem}_{safe_ext}"
            else:
                file_doc_id = f"{doc_id_str}-{safe_stem}"

            result = add_document(
                tmp_path,
                source_id=self.source_id,
                doc_id=file_doc_id,
                uri=file_link,          # direct RetrieveFile URL
                meta=kb_meta,
                creating_time=creating_time,
                update_time=update_time,
                copy_to_kb=True,
                force_reparse=self.force_reparse,
                skip_parse=self.skip_parse,
                session=session,
            )
            if result.get("document_ids"):
                document_ids.extend(result["document_ids"])
            if result.get("raw_document_id"):
                raw_document_ids.append(result["raw_document_id"])

            time.sleep(self.delay)

        # skip_parse leaves document_ids empty by design (only RawDocument
        # rows exist yet) — raw_document_ids is the success signal there.
        success_ids = raw_document_ids if self.skip_parse else document_ids
        return {
            "document_ids": document_ids,
            "raw_document_ids": raw_document_ids,
            "num_documents": len(document_ids),
            "parsed": len(document_ids) > 0,
            "error": None if success_ids else f"No files successfully ingested for doc {doc_id_str}",
        }
