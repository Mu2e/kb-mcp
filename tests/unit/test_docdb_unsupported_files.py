"""A DocDB entry holding only files we cannot parse is a skip, not a failure.

Entries like 58432 (a lone .zip) and 58450 (an .m4a plus an .mp4) used to
come back with an error, so every cron run that saw them was marked
"partial" until they aged out of the --days window.
"""
import io

import pytest

from kb_mcp.imports.docdb import DocDBSource


def _source(monkeypatch, files):
    src = DocDBSource(login=False, delay=0)
    monkeypatch.setattr(src, "get_meta", lambda doc_id: {"docid": doc_id, "title": "t", "files": files})

    def no_download(link):
        raise AssertionError(f"unsupported file was downloaded: {link}")

    monkeypatch.setattr(src, "_get_file", no_download)
    return src


def _file(name):
    return {"filename": name, "link": f"https://docdb.example/RetrieveFile?f={name}"}


def test_only_unsupported_files_is_skipped(monkeypatch, tmp_path):
    src = _source(monkeypatch, [_file("audio1.m4a"), _file("video1.mp4")])
    result = src.process_item({"id": 58450}, tmp_path, session=None)
    assert result["skipped"] is True
    assert result["error"] is None


def test_missing_link_alongside_unsupported_is_still_a_failure(monkeypatch, tmp_path):
    files = [_file("meeting.zip"), {"filename": "slides.pdf", "link": None}]
    src = _source(monkeypatch, files)
    result = src.process_item({"id": 58432}, tmp_path, session=None)
    assert not result.get("skipped")
    assert result["error"]


def test_supported_file_that_fails_is_still_a_failure(monkeypatch, tmp_path):
    src = _source(monkeypatch, [_file("meeting.zip"), _file("slides.pdf")])
    monkeypatch.setattr(src, "_get_file", lambda link: None)  # download fails
    result = src.process_item({"id": 1}, tmp_path, session=None)
    assert not result.get("skipped")
    assert result["error"]


class _Response:
    ok = True
    url = "https://docdb.example/RetrieveFile"

    def __init__(self, headers, body=b"<html><head><title>x</title></head></html>"):
        self.headers = headers
        self.content = body
        self.text = body.decode()


class _Session:
    def __init__(self, response):
        self.get = lambda *a, **k: response


def test_html_attachment_is_downloaded():
    # An exported notebook arrives as text/html, named in Content-Disposition.
    src = DocDBSource(login=False)
    resp = _Response({"Content-Type": "text/html; charset=UTF-8",
                      "Content-Disposition": 'filename="ResamplerVD101Comparison.html"'})
    src.session = _Session(resp)
    file_data = src._get_file(resp.url)
    assert file_data is not None
    assert file_data["document"].getvalue() == resp.content


def test_unnamed_html_is_still_treated_as_a_login_page():
    src = DocDBSource(login=False)
    resp = _Response({"Content-Type": "text/html;charset=utf-8"})
    src.session = _Session(resp)
    assert src._get_file(resp.url) is None


def test_html_file_reaches_add_document(monkeypatch, tmp_path):
    from kb_mcp.imports import docdb

    src = _source(monkeypatch, [_file("ResamplerVD101Comparison.html")])
    monkeypatch.setattr(src, "_get_file", lambda link: {"type": "html", "document": io.BytesIO(b"<html></html>")})
    added = []

    def fake_add_document(path, **kwargs):
        added.append(path.name)
        return {"document_ids": ["d1"], "raw_document_id": "r1"}

    monkeypatch.setattr(docdb, "add_document", fake_add_document)
    result = src.process_item({"id": 58263}, tmp_path, session=None)
    assert added == ["docdb-58263-ResamplerVD101Comparison.html"]
    assert result["error"] is None


@pytest.mark.parametrize("disposition", [
    'attachment; filename="notebook.html"',
    'attachment; Filename="notebook.html"',
    "attachment; filename*=UTF-8''notebook.html",
])
def test_html_attachment_header_variants(disposition):
    src = DocDBSource(login=False)
    resp = _Response({"Content-Type": "text/html", "Content-Disposition": disposition})
    src.session = _Session(resp)
    assert src._get_file(resp.url) is not None


def test_unsupported_skip_still_waits_before_the_next_request(monkeypatch, tmp_path):
    # An already-imported entry never reaches DocDB and owes no delay; an
    # entry skipped for unsupported files fetched its metadata, so it does.
    import contextlib
    import kb_mcp.kb as kb_mod
    from kb_mcp.imports import base

    src = DocDBSource(login=False, delay=0.5)
    results = iter([{"skipped": True}, {"skipped": True, "contacted_server": True}])
    monkeypatch.setattr(src, "process_item", lambda item, output_dir, session: next(results))
    session = type("S", (), {"commit": lambda self: None, "rollback": lambda self: None})()
    monkeypatch.setattr(kb_mod, "get_db_session", lambda: contextlib.nullcontext(session))
    sleeps = []
    monkeypatch.setattr(base.time, "sleep", sleeps.append)
    src._process_items_sequential([{"id": "1"}, {"id": "2"}], tmp_path, None, {"failures": []})
    assert sleeps == [0.5]
