"""Mu2e electronic logbook (ECL): import the documents attached to entries.

The ECL MCP server answers questions about the entries themselves; what it
cannot give an agent is the content of their attachments -- spreadsheets,
PDFs, Word files. This importer makes those searchable: every document-like
file attached to an entry becomes a raw document of source ``mu2e-ecl``, with
the entry's date, category, author, subject and the start of its text as
metadata, and a link back to the entry.

Access. ECL serves attachments (``/E/file/<fid>/<name>``) only to a logged-in
web session; signed API requests are refused there. ``/ECL/<exp>/authenticate``
logs a personal Fermilab Services account in (``X-Username``/``X-Password``,
checked against LDAP) and starts such a session, so this uses the same
account as the DocDB importer (MU2E_DOCDB_USERNAME / MU2E_DOCDB_PASSWORD).
The stable address (dbweb0) redirects to whichever server is up; the live one
is resolved first and the credentials are sent to it only, without following
redirects.

Entries are written once and rarely edited, so this works like the DocDB
import: a window of recent entries (``--days``), each attachment imported
once (``--skip-existing``). Size, 2026-11-23: 6,186 entries, 289 with files
(336 files, mostly xlsx and pdf), 816 with images (1,796).
"""

import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import requests

from .base import Source
from ..kb import add_document

logger = logging.getLogger(__name__)

#: Attachments worth parsing as documents (by extension). Old binary Office
#: formats (doc, xls, ppt) are left out: no parser reads them.
DOCUMENT_EXTENSIONS = {"pdf", "docx", "pptx", "xlsx", "csv", "txt", "md", "html", "htm"}

#: Image attachments, imported only with include_images.
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

#: How much of the entry's own text is kept with each attachment, as context.
ENTRY_TEXT_CHARS = 1500

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """ECL's "11/12/2026 13:17:14" (local time) as a naive datetime; None if unreadable."""
    try:
        return datetime.strptime((value or "").strip(), "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def entry_items(xml_text: str, include_images: bool = False,
                categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """The entries of an xml_search answer that carry attachments worth fetching.

    Each item: id, category, author, timestamp, subject, tags, text (plain,
    shortened), files, images. Pure: no network.
    """
    root = ET.fromstring(xml_text)
    wanted = {c.lower() for c in categories} if categories else None
    items = []
    for e in root.iter("entry"):
        files = int(e.get("files") or 0)
        images = int(e.get("images") or 0)
        if not files and not (include_images and images):
            continue
        category = e.get("category") or ""
        if wanted and category.lower() not in wanted:
            continue
        text_el = e.find("text")
        text = unescape(re.sub(r"<[^>]+>", " ", (text_el.text if text_el is not None else "") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        subject = e.find("subject")
        items.append({
            "id": int(e.get("id")),
            "category": category,
            "author": e.get("author") or "",
            "timestamp": e.get("timestamp") or "",
            "subject": (subject.text or "").strip() if subject is not None else "",
            "tags": [t.get("name") or (t.text or "").strip() for t in e.findall("tag")],
            "text": text[:ENTRY_TEXT_CHARS],
            "files": files,
            "images": images,
        })
    return items


def attachments_of(xml_text: str, include_images: bool = False) -> List[Dict[str, str]]:
    """The attachments of an xml_get answer worth importing: filename, type, path, ext."""
    out = []
    for a in ET.fromstring(xml_text).iter("attachment"):
        name = a.get("filename") or ""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        kind = a.get("type") or ""
        if ext in DOCUMENT_EXTENSIONS:
            pass
        elif include_images and ext in IMAGE_EXTENSIONS:
            pass
        else:
            continue
        path = urlparse(a.get("url") or "").path
        if "/E/file/" not in path and "/E/image/" not in path:
            continue
        out.append({"filename": name, "type": kind, "path": path, "ext": ext})
    return out


def attachment_doc_id(entry_id: int, filename: str) -> str:
    """``<entry>-<stem>_<ext>``, as DocDB names its files: an entry's attachments
    sort together, and ``{"wildcard": {"doc_id": "6098-*"}}`` finds all of them."""
    stem, dot, ext = filename.rpartition(".")
    name = f"{stem}_{ext}" if dot and stem else filename
    return f"{entry_id}-{_SAFE_NAME.sub('_', name).strip('_')}"


def entry_details(xml_text: str) -> Dict[str, Any]:
    """What xml_get says about the entry itself: author, category, timestamp, text, private."""
    root = ET.fromstring(xml_text)
    text = next((f.text or "" for f in root.iter("field") if f.get("name") == "text"), "")
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()
    private = (root.get("private") or "").strip().lower()
    return {"author": root.get("author") or "", "category": root.get("category") or "",
            "timestamp": root.get("timestamp") or "", "text": text[:ENTRY_TEXT_CHARS],
            "private": private not in ("", "no", "0", "false", "none")}


class ECLSource(Source):
    """Documents attached to Mu2e logbook entries (source ``mu2e-ecl``)."""

    def __init__(
        self,
        url: str = "https://dbweb0.fnal.gov/ECL/mu2e",
        source_id: str = "mu2e-ecl",
        days: int = 2,
        categories: Optional[List[str]] = None,
        include_images: bool = False,
        skip_existing: bool = False,
        delay: float = 0.5,
        timeout: float = 60.0,
        login: bool = True,
    ):
        super().__init__(
            source_id=source_id,
            name="ecl",
            description="Mu2e electronic logbook (ECL): documents attached to entries",
            base_uri=url,
            delay=delay,
            timeout=timeout,
            meta={"importer": "ecl.py", "ecl_url": url},
        )
        self.url = url.rstrip("/")
        self.days = days
        self.categories = categories
        self.include_images = include_images
        self.skip_existing = skip_existing
        self.session: Optional[requests.Session] = None
        self.live_base: Optional[str] = None
        if login:
            self._login()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.session is not None:
            self.session.close()
            self.session = None

    # -- access ------------------------------------------------------------------

    def _login(self) -> None:
        user, password = os.getenv("MU2E_DOCDB_USERNAME"), os.getenv("MU2E_DOCDB_PASSWORD")
        if not user or not password:
            raise RuntimeError("The ECL importer needs MU2E_DOCDB_USERNAME and MU2E_DOCDB_PASSWORD "
                               "(the Fermilab Services account, in .env.local)")
        s = requests.Session()
        s.headers["User-Agent"] = "kb-mcp-ecl-import"
        # The stable address redirects to a live server; resolve it without credentials.
        r = s.get(f"{self.url}/E/index", timeout=self.timeout)
        live = urlparse(r.url)
        path = urlparse(self.url).path.rstrip("/")
        self.live_base = f"https://{live.netloc}{path}"
        r = s.get(f"{self.live_base}/authenticate", headers={"X-Username": user, "X-Password": password},
                  allow_redirects=False, timeout=self.timeout)
        if r.status_code != 200 or not r.text.startswith("OK"):
            s.close()
            answer = re.sub(r"session_key: \S+", "", r.text)[:80]
            raise RuntimeError(f"ECL login failed at {live.netloc}: HTTP {r.status_code} {answer!r}")
        self.session = s
        logger.info(f"Logged in to ECL at {live.netloc}")

    def entry_url(self, entry_id: int) -> str:
        """The entry's page, at the stable address (people and links use that one)."""
        return f"{self.url}/E/show?e={entry_id}"

    # -- items -------------------------------------------------------------------

    def fetch_items(self, query: Optional[str] = None, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Entries of the last `days` days with attachments; `query` may name entry ids ("6098,6100")."""
        if query and re.fullmatch(r"[\d,\s]+", query):
            ids = [int(x) for x in re.split(r"[,\s]+", query) if x]
            items = []
            for entry_id in ids:
                r = self.session.get(f"{self.live_base}/E/xml_get", params={"e": entry_id}, timeout=self.timeout)
                r.raise_for_status()
                items.append({"id": entry_id, "category": "", "author": "", "timestamp": "", "subject": "",
                              "tags": [], "text": "", "files": 1, "images": 0})
            return items
        r = self.session.get(f"{self.live_base}/E/xml_search",
                             params={"a": f"{int(self.days)}days", "l": "100000"}, timeout=max(self.timeout, 600))
        r.raise_for_status()
        items = entry_items(r.text, include_images=self.include_images, categories=self.categories)
        logger.info(f"ECL: {len(items)} entries with attachments in the last {self.days} days")
        return items[:max_results] if max_results else items

    def process_item(self, item: Dict[str, Any], output_dir: Path, session: Any) -> Dict[str, Any]:
        """Fetch one entry's attachments and add the document-like ones."""
        from ..kb.db_models import RawDocument

        entry_id = int(item["id"])
        r = self.session.get(f"{self.live_base}/E/xml_get", params={"e": entry_id}, timeout=self.timeout)
        if r.status_code != 200:
            return {"document_ids": [], "num_documents": 0, "parsed": False,
                    "error": f"xml_get for entry {entry_id}: HTTP {r.status_code}"}
        details = entry_details(r.text)
        if details["private"]:
            # A private entry is not for everyone who can read the logbook.
            logger.info(f"Entry {entry_id} is private: skipped")
            return {"document_ids": [], "num_documents": 0, "parsed": False, "skipped": True,
                    "contacted_server": True, "error": None}
        # The search gives these too; for entries named by id only xml_get does.
        item = {**item, **{k: v for k, v in details.items() if v and not item.get(k)}}
        attachments = attachments_of(r.text, include_images=self.include_images)
        if not attachments:
            return {"document_ids": [], "num_documents": 0, "parsed": False, "skipped": True,
                    "contacted_server": True, "error": None}

        ts = parse_timestamp(item.get("timestamp"))
        result = {"document_ids": [], "num_documents": 0, "parsed": False, "error": None}
        added = 0
        for att in attachments:
            doc_id = attachment_doc_id(entry_id, att["filename"])
            if self.skip_existing and session.query(RawDocument.id).filter(
                    RawDocument.source_id == self.source_id, RawDocument.doc_id == doc_id).first():
                continue
            # The url from xml_get is already percent-encoded; normalise rather than encode twice.
            f = self.session.get(f"https://{urlparse(self.live_base).netloc}{quote(unquote(att['path']))}",
                                 timeout=max(self.timeout, 300))
            body = f.content
            # A login page instead of the file means the session did not reach it.
            login_page = body[:15].lstrip().lower().startswith((b"<!doctype", b"<html")) \
                and att["ext"] not in ("html", "htm")
            if f.status_code != 200 or login_page:
                logger.warning(f"Entry {entry_id}: could not fetch {att['filename']} (HTTP {f.status_code})")
                result["error"] = f"could not fetch {att['filename']} (HTTP {f.status_code})"
                continue
            entry_dir = Path(output_dir) / str(entry_id)
            entry_dir.mkdir(parents=True, exist_ok=True)
            path = entry_dir / (_SAFE_NAME.sub("_", att["filename"]).strip("_") or f"attachment.{att['ext']}")
            path.write_bytes(body)
            meta = {
                "title": f"{item.get('subject') or att['filename']} (ECL {entry_id})",
                "ecl_entry": entry_id,
                "ecl_category": item.get("category") or None,
                "ecl_author": item.get("author") or None,
                "ecl_subject": item.get("subject") or None,
                "ecl_tags": item.get("tags") or [],
                "ecl_entry_text": item.get("text") or None,
                "ecl_filename": att["filename"],
                "ecl_time": ts.isoformat() if ts else None,
            }
            res = add_document(path, source_id=self.source_id, doc_id=doc_id, uri=self.entry_url(entry_id),
                               meta={k: v for k, v in meta.items() if v not in (None, [], "")},
                               creating_time=ts, update_time=ts, copy_to_kb=True, session=session)
            result["document_ids"].extend(res.get("document_ids") or [])
            result["parsed"] = result["parsed"] or bool(res.get("parsed"))
            added += 1
            logger.info(f"Entry {entry_id}: added {att['filename']} ({len(body) // 1024} KB)")
        result["num_documents"] = len(result["document_ids"])
        if not added and not result["error"]:
            result["skipped"] = True
        return result
