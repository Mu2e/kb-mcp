"""Regression tests for source timestamps reaching the Document columns.

Every importer dropped the source system's own dates on the floor. Nothing
converted them, and `add_document` had no way to set the `creating_time` /
`update_time` columns at all, so ingested rows landed with both columns NULL
across all sources (24k DocDB, 4.9k INSPIRE, 536 wiki). The only usable date
was `insert_time` - when we happened to scrape a document, not when it was
written - leaving date-range search and the web UI's Creation/Update sort
options with nothing to filter on.

Each source needs different handling, which is what these tests pin:

* DocDB renders Fermilab local time with no zone marker ("10 Apr 2026,
  16:28"), so parsing must anchor to America/Chicago before storing UTC or
  every timestamp is off by the 5-6 hour offset.
* MediaWiki returns ISO-8601 with a trailing "Z", and needs a separate API
  call per direction because `rvdir` only walks one way.
* INSPIRE mixes full ISO timestamps with partial `preprint_date` values
  ("YYYY", "YYYY-MM", "YYYY-MM-DD") that must not crash the import.

The propagation tests use a stubbed parse/persistence layer: they pin that
add_document stamps *every* document it produces - including the image and
table records the parser builds independently of the main document dict -
rather than exercising a real parse.
"""

from datetime import datetime, timezone

import pytest

from kb_mcp.imports.docdb import parse_docdb_datetime
from kb_mcp.imports.inspire import parse_inspire_date
from kb_mcp.imports.mediawiki import parse_wiki_timestamp


class TestParseDocdbDatetime:
    def test_parses_central_daylight_time_to_utc(self):
        # April is CDT (UTC-5).
        assert parse_docdb_datetime("10 Apr 2026, 16:28") == datetime(
            2026, 4, 10, 21, 28, tzinfo=timezone.utc
        )

    def test_parses_central_standard_time_to_utc(self):
        # January is CST (UTC-6), a different offset from the CDT case.
        assert parse_docdb_datetime("1 Jan 2010, 00:00") == datetime(
            2010, 1, 1, 6, 0, tzinfo=timezone.utc
        )

    def test_result_is_timezone_aware(self):
        # The columns are DateTime(timezone=True); a naive value would be
        # interpreted as server-local time on write.
        assert parse_docdb_datetime("15 Jul 2015, 13:45").tzinfo is not None

    def test_tolerates_surrounding_whitespace(self):
        assert parse_docdb_datetime("  10 Apr 2026, 16:28  ") == datetime(
            2026, 4, 10, 21, 28, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("value", [None, "", "garbage", "2026-04-10"])
    def test_unparseable_values_return_none(self, value):
        # Timestamps are nice-to-have metadata: a surprise format must not
        # abort an import that otherwise succeeded.
        assert parse_docdb_datetime(value) is None


CREATED = datetime(2026, 4, 10, 21, 28, tzinfo=timezone.utc)
UPDATED = datetime(2026, 4, 11, 15, 0, tzinfo=timezone.utc)


def _run_add_document(monkeypatch, tmp_path, *, already_ingested, force_reparse):
    """Drive add_document with the DB layer stubbed out.

    `already_ingested` simulates the content-hash collision path: DocDB
    re-serves byte-identical files, so a second run of the importer over the
    same document hits it. Returns the list of Documents handed to
    add_parsed_many - empty when add_document took its skip path.
    """
    import contextlib

    from kb_mcp.kb.documents import operations

    src = tmp_path / "doc.txt"
    src.write_text("body text")

    # parse() returns the main dict plus the table/image records that the
    # parser builds on its own - none of which carry timestamps.
    def fake_parse(path, data=None, **kwargs):
        base = {"source_id": data["source_id"], "doc_id": data["doc_id"]}
        return [
            {**base, "text": "body text", "doc_type": "text", "meta": {}},
            {**base, "text": "a table", "doc_type": "table", "meta": {}},
            {**base, "text": "a figure", "doc_type": "image", "meta": {}},
        ]

    monkeypatch.setattr("kb_mcp.parser.parse", fake_parse)

    captured = []

    def fake_add_parsed_many(documents, dedup_level=None, session=None):
        captured.extend(documents)
        return documents

    monkeypatch.setattr(operations, "add_parsed_many", fake_add_parsed_many)
    monkeypatch.setattr(operations, "_ensure_db_initialized", lambda: None)
    # insert_raw_document returns None when the content_hash already exists.
    monkeypatch.setattr(
        operations,
        "insert_raw_document",
        lambda **kwargs: None if already_ingested else "raw-1",
    )
    monkeypatch.setattr(
        operations,
        "get_or_create_parser",
        lambda **kwargs: type("P", (), {"name": "kb-mcp"})(),
    )

    class _Row:
        id = "raw-1"

    class _Query:
        def filter(self, *a, **k):
            return self

        def first(self):
            # Serves both the RawDocument and the Document-for-parser lookups;
            # a truthy row for each is what drives the skip branch.
            return _Row()

    class _Session:
        def commit(self):
            pass

        def query(self, *a, **k):
            if not already_ingested:
                raise AssertionError("no lookup expected for a fresh insert")
            return _Query()

    monkeypatch.setattr(
        operations,
        "get_db_session",
        lambda s=None: contextlib.nullcontext(_Session()),
    )

    result = operations.add_document(
        src,
        source_id="mu2e-docdb",
        doc_id="56359-MB-374",
        parser_name="kb-mcp",
        creating_time=CREATED,
        update_time=UPDATED,
        force_reparse=force_reparse,
    )
    return captured, result


class TestAddDocumentStampsEveryDocument:
    """add_document must apply the source timestamps to child records too."""

    def test_all_produced_documents_get_both_timestamps(self, monkeypatch, tmp_path):
        captured, _ = _run_add_document(
            monkeypatch, tmp_path, already_ingested=False, force_reparse=False
        )

        assert len(captured) == 3, "main document plus table and image records"
        for doc in captured:
            assert doc.creating_time == CREATED
            assert doc.update_time == UPDATED

    def test_already_ingested_document_is_skipped_entirely(self, monkeypatch, tmp_path):
        """A re-run over unchanged files does NOT backfill the timestamps.

        add_document returns early on the content-hash + same-parser match,
        before anything is stamped. This is the pre-existing skip contract,
        pinned here because it is exactly what makes a plain re-import a
        no-op for the 24k rows already in the database.
        """
        captured, result = _run_add_document(
            monkeypatch, tmp_path, already_ingested=True, force_reparse=False
        )

        assert result["skipped"] is True
        assert captured == [], "nothing re-parsed, so nothing re-stamped"

    def test_force_reparse_backfills_an_existing_document(self, monkeypatch, tmp_path):
        """--force-reparse is the supported way to backfill existing rows."""
        captured, result = _run_add_document(
            monkeypatch, tmp_path, already_ingested=True, force_reparse=True
        )

        assert result["skipped"] is False
        assert len(captured) == 3
        for doc in captured:
            assert doc.creating_time == CREATED
            assert doc.update_time == UPDATED


class TestParseWikiTimestamp:
    def test_parses_iso_z_suffix_to_utc(self):
        assert parse_wiki_timestamp("2016-12-28T22:34:43Z") == datetime(
            2016, 12, 28, 22, 34, 43, tzinfo=timezone.utc
        )

    def test_result_is_timezone_aware(self):
        assert parse_wiki_timestamp("2024-10-21T15:46:13Z").tzinfo is not None

    @pytest.mark.parametrize("value", [None, "", "junk"])
    def test_unparseable_values_return_none(self, value):
        assert parse_wiki_timestamp(value) is None


class TestGetRevisionTimes:
    """The wiki importer must ask the API for revisions at all.

    `action=parse`, which fetches the page content, carries no revision
    data whatsoever - the original importer only ever made that call, which
    is why no wiki page had timestamps. These tests pin that both
    directions are queried and that a page with no history degrades to
    None rather than raising.
    """

    def _source(self, monkeypatch, responses):
        from kb_mcp.imports import mediawiki

        src = mediawiki.MediaWikiSource(use_kerberos=False)
        calls = []

        def fake_api_request(params):
            calls.append(params)
            return responses.get(params.get("rvdir"))

        monkeypatch.setattr(src, "_api_request", fake_api_request)
        return src, calls

    def test_queries_oldest_and_newest_revisions(self, monkeypatch):
        def page(ts):
            return {"query": {"pages": {"20": {"revisions": [{"timestamp": ts}]}}}}

        src, calls = self._source(
            monkeypatch,
            {
                "newer": page("2016-12-28T22:34:43Z"),
                "older": page("2024-10-21T15:46:13Z"),
            },
        )

        times = src._get_revision_times("Computing")

        # rvdir=newer walks from the start of history (creation);
        # rvdir=older walks back from the tip (last edit).
        assert [c["rvdir"] for c in calls] == ["newer", "older"]
        assert all(c["action"] == "query" for c in calls)
        assert times["created"] == datetime(2016, 12, 28, 22, 34, 43, tzinfo=timezone.utc)
        assert times["updated"] == datetime(2024, 10, 21, 15, 46, 13, tzinfo=timezone.utc)

    def test_missing_page_yields_no_timestamps(self, monkeypatch):
        # Deleted/missing pages come back with no "revisions" key.
        missing = {"query": {"pages": {"-1": {"missing": ""}}}}
        src, _ = self._source(monkeypatch, {"newer": missing, "older": missing})

        assert src._get_revision_times("NoSuchPage") == {
            "created": None,
            "updated": None,
        }

    def test_api_failure_does_not_raise(self, monkeypatch):
        # _api_request returns None on curl/JSON failure; an unreachable
        # history must not abort an otherwise fine import.
        src, _ = self._source(monkeypatch, {"newer": None, "older": None})

        assert src._get_revision_times("Computing") == {
            "created": None,
            "updated": None,
        }


class TestParseInspireDate:
    def test_parses_full_iso_timestamp(self):
        assert parse_inspire_date("2023-03-10T15:19:10.632902+00:00") == datetime(
            2023, 3, 10, 15, 19, 10, 632902, tzinfo=timezone.utc
        )

    def test_parses_full_date(self):
        assert parse_inspire_date("2022-10-25") == datetime(
            2022, 10, 25, tzinfo=timezone.utc
        )

    def test_year_only_anchors_to_january_first(self):
        # INSPIRE records only the precision it knows; a bare year must
        # still sort and range-filter sensibly.
        assert parse_inspire_date("2012") == datetime(2012, 1, 1, tzinfo=timezone.utc)

    def test_year_month_anchors_to_first_of_month(self):
        assert parse_inspire_date("2014-10") == datetime(
            2014, 10, 1, tzinfo=timezone.utc
        )

    def test_result_is_timezone_aware(self):
        assert parse_inspire_date("2012").tzinfo is not None

    @pytest.mark.parametrize("value", [None, "", "  ", "junk", "2012-13"])
    def test_unparseable_values_return_none(self, value):
        # "2012-13" is a real hazard: it looks like YYYY-MM but month 13
        # does not exist, and must not raise.
        assert parse_inspire_date(value) is None
