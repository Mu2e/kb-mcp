"""A DocDB entry holding only files we cannot parse is a skip, not a failure.

Entries like 58432 (a lone .zip) and 58450 (an .m4a plus an .mp4) used to
come back with an error, so every cron run that saw them was marked
"partial" until they aged out of the --days window.
"""
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
