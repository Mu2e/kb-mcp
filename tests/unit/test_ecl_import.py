"""ECL importer: which entries and attachments, their ids and metadata. No network, no database."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kb_mcp.imports import ecl

SEARCH = """<entries>
  <entry id="6098" author="Some One" category="Planning" timestamp="11/12/2026 13:17:14" images="0" files="1">
    <subject>Work request</subject><tag name="cryo"/><text>Line one &amp; &lt;b&gt;two&lt;/b&gt;
    and more</text></entry>
  <entry id="6099" author="A" category="Tracker" timestamp="11/13/2026 08:00:00" images="2" files="0"><text>plots</text></entry>
  <entry id="6100" author="B" category="Planning" timestamp="11/14/2026 09:00:00" images="0" files="0"><text>no attachments</text></entry>
</entries>"""

GET = """<entry author="Some One" category="Planning" timestamp="11/12/2026 13:17:14" private="no">
  <form name="default"><field name="text">Weekly &lt;i&gt;plan&lt;/i&gt;</field></form>
  <attachment type="file" filename="Work request Spreadsheet_20261112.xlsx" index="0"
              url="http://dbweb1.fnal.gov:8443/ECL/mu2e/E/file/123/Work%20request%20Spreadsheet_20261112.xlsx"/>
  <attachment type="file" filename="old.xls" index="1" url="http://h:8443/ECL/mu2e/E/file/124/old.xls"/>
  <attachment type="file" filename="photo.heic" index="2" url="http://h:8443/ECL/mu2e/E/file/125/photo.heic"/>
  <attachment type="image" filename="plot.png" index="3" url="http://h:8443/ECL/mu2e/E/image/126/plot.png"/>
</entry>"""


def test_only_entries_with_attachments():
    items = ecl.entry_items(SEARCH)
    assert [i["id"] for i in items] == [6098]
    i = items[0]
    assert i["category"] == "Planning" and i["subject"] == "Work request" and i["tags"] == ["cryo"]
    assert i["text"] == "Line one & two and more"                 # markup and whitespace removed
    assert [x["id"] for x in ecl.entry_items(SEARCH, include_images=True)] == [6098, 6099]
    assert [x["id"] for x in ecl.entry_items(SEARCH, include_images=True, categories=["tracker"])] == [6099]


def test_document_attachments_and_images_on_request():
    docs = ecl.attachments_of(GET)
    assert [a["filename"] for a in docs] == ["Work request Spreadsheet_20261112.xlsx"]   # no xls, no heic
    assert docs[0]["path"] == "/ECL/mu2e/E/file/123/Work%20request%20Spreadsheet_20261112.xlsx"
    with_images = ecl.attachments_of(GET, include_images=True)
    assert [a["filename"] for a in with_images] == ["Work request Spreadsheet_20261112.xlsx", "plot.png"]


def test_ids_and_timestamps():
    assert ecl.attachment_doc_id(6098, "Work request Spreadsheet_20261112.xlsx") == "6098-Work_request_Spreadsheet_20261112_xlsx"
    assert ecl.attachment_doc_id(7, "notes") == "7-notes"
    assert ecl.parse_timestamp("11/12/2026 13:17:14") == datetime(2026, 11, 12, 13, 17, 14)
    assert ecl.parse_timestamp("") is None and ecl.parse_timestamp("2026-11-12") is None


class _HTTP:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/E/xml_get"):
            return SimpleNamespace(status_code=200, text=GET)
        body = self.files.get(url.split("/E/file/", 1)[-1])
        return SimpleNamespace(status_code=200 if body else 404, content=body or b"")


class _DB:
    def __init__(self, existing=(), raws=None):
        self.existing = set(existing)
        self.raws = raws or {}

    def get(self, model, key):
        return self.raws.get(key)

    def query(self, *a):
        db = self

        class Q:
            def filter(self, *conds):
                self.doc_id = next((c.right.value for c in conds if getattr(c.left, "key", "") == "doc_id"), None)
                return self

            def first(self):
                return ("raw",) if self.doc_id in db.existing else None
        return Q()


def _source(http):
    src = ecl.ECLSource(login=False)
    src.session, src.live_base = http, "https://dbweb1.fnal.gov:8443/ECL/mu2e"
    return src


ITEM = ecl.entry_items(SEARCH)[0]


def test_an_attachment_is_added_with_entry_metadata(tmp_path, monkeypatch):
    added = []
    monkeypatch.setattr(ecl, "add_document", lambda path, **kw: (added.append((Path(path), kw)),
                                                                 {"document_ids": ["d1"], "parsed": True})[1])
    http = _HTTP({"123/Work%20request%20Spreadsheet_20261112.xlsx": b"PK\x03\x04 spreadsheet"})
    res = _source(http).process_item(ITEM, tmp_path, _DB())
    assert res["error"] is None and res["document_ids"] == ["d1"]
    (path, kw), = added
    assert path.read_bytes().startswith(b"PK") and path.parent.name == "6098"
    assert kw["doc_id"] == "6098-Work_request_Spreadsheet_20261112_xlsx" and kw["source_id"] == "mu2e-ecl"
    assert kw["uri"] == "https://dbweb0.fnal.gov/ECL/mu2e/E/show?e=6098"          # stable address, not the live host
    assert kw["meta"]["ecl_category"] == "Planning" and kw["meta"]["ecl_entry"] == 6098
    assert kw["meta"]["title"] == "Work request (ECL 6098)" and kw["creating_time"] == datetime(2026, 11, 12, 13, 17, 14)
    assert http.calls[-1].startswith("https://dbweb1.fnal.gov:8443/ECL/mu2e/E/file/123/")   # https on the live host


def test_already_imported_attachments_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ecl, "add_document", lambda *a, **k: pytest.fail("must not re-import"))
    src = _source(_HTTP({}))
    src.skip_existing = True
    res = src.process_item(ITEM, tmp_path, _DB(existing={"6098-Work_request_Spreadsheet_20261112_xlsx"}))
    assert res.get("skipped") and res["error"] is None


def test_a_login_page_instead_of_the_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ecl, "add_document", lambda *a, **k: pytest.fail("must not import a login page"))
    http = _HTTP({"123/Work%20request%20Spreadsheet_20261112.xlsx": b"<!DOCTYPE html><html>login"})
    res = _source(http).process_item(ITEM, tmp_path, _DB())
    assert "could not fetch" in res["error"]


def test_login_needs_the_services_account(monkeypatch):
    monkeypatch.delenv("MU2E_DOCDB_USERNAME", raising=False)
    with pytest.raises(RuntimeError, match="MU2E_DOCDB_USERNAME"):
        ecl.ECLSource()


def test_entries_named_by_id_get_their_details_from_xml_get(tmp_path, monkeypatch):
    added = []
    monkeypatch.setattr(ecl, "add_document", lambda path, **kw: (added.append(kw), {"document_ids": ["d"], "parsed": True})[1])
    http = _HTTP({"123/Work%20request%20Spreadsheet_20261112.xlsx": b"PK\x03\x04"})
    bare = {"id": 6098, "category": "", "author": "", "timestamp": "", "subject": "", "tags": [], "text": ""}
    _source(http).process_item(bare, tmp_path, _DB())
    meta = added[0]["meta"]
    assert meta["ecl_category"] == "Planning" and meta["ecl_author"] == "Some One"
    assert meta["ecl_entry_text"] == "Weekly plan" and added[0]["creating_time"] == datetime(2026, 11, 12, 13, 17, 14)


def test_private_entries_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(ecl, "add_document", lambda *a, **k: pytest.fail("a private entry must not be imported"))
    http = _HTTP({})
    http.get = lambda url, params=None, timeout=None: SimpleNamespace(status_code=200, text=GET.replace('private="no"', 'private="yes"'))
    res = _source(http).process_item(ITEM, tmp_path, _DB())
    assert res.get("skipped") and res["error"] is None


def test_a_file_already_in_the_kb_from_another_source_is_not_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(ecl, "add_document", lambda path, **kw: {"raw_document_id": "r-docdb", "document_ids": [], "parsed": False})
    http = _HTTP({"123/Work%20request%20Spreadsheet_20261112.xlsx": b"PK\x03\x04"})
    db = _DB(raws={"r-docdb": SimpleNamespace(source_id="mu2e-docdb", doc_id="1234-plan_xlsx")})
    res = _source(http).process_item(ITEM, tmp_path, db)
    assert res.get("skipped") and res["error"] is None and res["document_ids"] == []
