"""kb_search with an empty query lists what the filter passes, newest first.

The listing itself runs against throwaway SQLite (plain columns, no
Postgres-only features); kb_search's routing is checked with the listing
replaced, so no embedder or database is needed for it.
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import kb_mcp.server.mcp as mcp
from kb_mcp.kb.db_models import Base, Document, DocumentVisibilityCurrent, RawDocument, Source
from kb_mcp.kb.search import search_filter_only as fo


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/filter_only.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Source(id="mu2e-docdb"))
    t0 = datetime(2026, 11, 1)
    for i in range(6):
        raw = f"raw-{i}"
        s.add(RawDocument(id=raw, source_id="mu2e-docdb", source_type="application/pdf", content_hash=f"h{i}"))
        s.add(Document(id=f"img-{i}", source_id="mu2e-docdb", raw_document_id=raw, doc_id=f"100{i}-fig",
                       doc_type="image", source_type="image/png", text=f"figure {i}",
                       insert_time=t0 + timedelta(days=i)))
        s.add(Document(id=f"txt-{i}", source_id="mu2e-docdb", raw_document_id=raw, doc_id=f"100{i}-talk",
                       doc_type="text", source_type="application/pdf", text=f"talk {i}",
                       insert_time=t0 + timedelta(days=i)))
    # raw-5 is hidden from search: its newest image must not be listed.
    s.add(DocumentVisibilityCurrent(raw_document_id="raw-5", group_name="*",
                                    visible_search=False, visible_get=True))
    s.flush()
    yield s
    s.close()


def ids(response):
    return [r["document"].id for r in response["results"]]


def test_newest_first_within_the_filter(session):
    out = fo.search_filter_only(filter={"term": {"doc_type": "image"}}, max_results=3, session=session)
    assert ids(out) == ["img-5", "img-4", "img-3"]
    assert all(r["chunks"] == [] for r in out["results"])
    assert out["metadata"]["path"] == "narrow"
    assert out["metadata"]["search_type"] == "filter_only"


def test_visibility_drops_hidden_documents(session):
    out = fo.search_filter_only(filter={"term": {"doc_type": "image"}}, max_results=3,
                                enforce_visibility=True, session=session)
    assert ids(out) == ["img-4", "img-3", "img-2"]


def test_a_broad_filter_takes_the_ordered_query_with_the_same_answer(session, monkeypatch):
    monkeypatch.setattr(fo, "CANDIDATE_CAP", 2)          # 6 images pass: "broad"
    out = fo.search_filter_only(filter={"term": {"doc_type": "image"}}, max_results=3,
                                enforce_visibility=True, session=session)
    assert out["metadata"]["path"] == "broad"
    assert ids(out) == ["img-4", "img-3", "img-2"]


def test_a_narrow_filter_lists_only_its_documents(session):
    out = fo.search_filter_only(filter={"wildcard": {"doc_id": "1002-*"}}, max_results=10, session=session)
    assert sorted(ids(out)) == ["img-2", "txt-2"]


# -- routing in kb_search --------------------------------------------------------


@pytest.fixture()
def listing(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return {"results": [{"document": object(), "chunks": []}], "metadata": {"order": fo.ORDER}}

    monkeypatch.setattr(fo, "search_filter_only", fake)
    monkeypatch.setattr(mcp, "_format_search_results", lambda results: [{"text": "x"} for _ in results])
    return calls


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_with_a_doc_type_filter_lists(listing, query):
    out = json.loads(mcp.kb_search(query=query, max_results=4, search_filter='{"term": {"doc_type": "image"}}'))
    assert out["count"] == 1 and out["order"] == fo.ORDER
    (call,) = listing
    assert call["filter"] == {"term": {"doc_type": "image"}}
    assert call["max_results"] == 4 and call["enforce_visibility"] is True


def test_empty_query_leaves_images_out_unless_the_filter_names_doc_type(listing):
    mcp.kb_search(query="", search_filter={"term": {"source_id": "mu2e-docdb"}})
    (call,) = listing
    assert call["filter"] == {"bool": {"must": [{"term": {"source_id": "mu2e-docdb"}}],
                                       "must_not": [{"term": {"doc_type": "image"}}]}}
    assert call["max_results"] == mcp.DEFAULT_MAX_RESULTS


def test_empty_query_without_a_filter_is_an_error(listing):
    out = json.loads(mcp.kb_search(query=""))
    assert "error" in out and listing == []


def test_a_bad_filter_is_still_reported_before_listing(listing):
    out = json.loads(mcp.kb_search(query="", search_filter={"nope": {}}))
    assert out["error"].startswith("Invalid search_filter") and listing == []
