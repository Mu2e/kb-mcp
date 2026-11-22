"""Wiki sync: pages are living documents, kept at their current version.

Covers the decisions without a wiki or a database where possible: the sync
plan (and its guards against deleting a whole source on a broken page list),
image inlining, the page listing's paging, and the dry run; plus the two KB
helpers a page update uses, against throwaway SQLite.
"""

import base64
from datetime import datetime
from types import SimpleNamespace

import pytest

from kb_mcp.imports import mediawiki as mw


def remote(page_id, title, rev):
    return {"page_id": page_id, "title": title, "revision_id": rev}


def local(raw_id, page_id, doc_id, rev):
    return {"raw_id": raw_id, "page_id": page_id, "doc_id": doc_id, "revision_id": rev}


# -- plan_sync ---------------------------------------------------------------------

def test_new_changed_unchanged_renamed_deleted():
    wiki = [remote(1, "Main Page", 10), remote(2, "Computing", 21), remote(3, "Tracker/Calibration", 30),
            remote(4, "New page", 40), remote(6, "Renamed", 60)] + [remote(100 + i, f"P{i}", 1) for i in range(20)]
    kb = [local("r1", 1, "Main_Page", 10), local("r2", 2, "Computing", 20), local("r3", "3", "Tracker/Calibration", "30"),
          local("r5", 5, "Gone", 50), local("r6", 6, "Old_name", 60)] + [local(f"x{i}", 100 + i, f"P{i}", 1) for i in range(20)]
    plan = mw.plan_sync(wiki, kb)
    assert [p["title"] for p in plan["new"]] == ["New page"]
    changed = {p["title"]: p for p in plan["changed"]}
    assert set(changed) == {"Computing", "Renamed"}                       # new revision; renamed
    assert changed["Computing"]["raw_id"] == "r2" and changed["Renamed"]["old_doc_id"] == "Old_name"
    assert "Tracker/Calibration" in [p["title"] for p in plan["unchanged"]]   # ids compare as text
    assert [g["raw_id"] for g in plan["deleted"]] == ["r5"]
    assert plan["deletions_blocked"] is None and plan["remote_count"] == 25


def test_full_reimports_every_known_page():
    plan = mw.plan_sync([remote(1, "A", 1), remote(2, "B", 2)], [local("r1", 1, "A", 1)], full=True)
    assert [p["raw_id"] for p in plan["changed"]] == ["r1"] and not plan["unchanged"]
    assert [p["title"] for p in plan["new"]] == ["B"]


def test_a_failed_listing_deletes_nothing():
    plan = mw.plan_sync(None, [local("r1", 1, "A", 1)])
    assert plan["deleted"] == [] and "could not be read" in plan["deletions_blocked"]
    assert plan["new"] == plan["changed"] == []


def test_an_empty_wiki_deletes_nothing():
    plan = mw.plan_sync([], [local("r1", 1, "A", 1)])
    assert plan["deleted"] == [] and "no pages" in plan["deletions_blocked"]


def test_too_many_deletions_are_blocked():
    kb = [local(f"r{i}", i, f"P{i}", 1) for i in range(100)]
    wiki = [remote(i, f"P{i}", 1) for i in range(85)]                   # 15 % would go
    plan = mw.plan_sync(wiki, kb)
    assert plan["deleted"] == [] and "15 of 100" in plan["deletions_blocked"]
    assert len(mw.plan_sync(wiki, kb, max_delete_fraction=0.2)["deleted"]) == 15


def test_one_page_can_go_from_a_small_wiki():
    plan = mw.plan_sync([remote(1, "A", 1)], [local("r1", 1, "A", 1), local("r2", 2, "B", 1)])
    assert [g["raw_id"] for g in plan["deleted"]] == ["r2"]


def test_raws_without_a_page_id_are_left_alone():
    plan = mw.plan_sync([remote(1, "A", 1)], [local("r1", 1, "A", 1), local("old", None, "Legacy", None)])
    assert plan["deleted"] == [] and plan["deletions_blocked"] is None


# -- images ------------------------------------------------------------------------

def test_thumbnails_resolve_to_the_original():
    assert mw.original_image_url("https://w/images/thumb/a/ab/Plot.png/300px-Plot.png") == "https://w/images/a/ab/Plot.png"
    assert mw.original_image_url("https://w/images/a/ab/Plot.png") == "https://w/images/a/ab/Plot.png"


def test_images_of_the_wiki_are_inlined_once_each():
    html = ('<p><img src="/images/thumb/a/ab/Plot.png/300px-Plot.png" srcset="x 2x" width="300">'
            '<img src="/images/thumb/a/ab/Plot.png/600px-Plot.png">'
            '<img src="https://elsewhere.org/logo.png"><img src="/images/c/cd/Huge.png"></p>')
    fetched = []

    def fetch(url):
        fetched.append(url)
        return b"\x89PNG small" if "Plot" in url else b"x" * (mw.MAX_INLINE_IMAGE_BYTES + 1)

    out, count = mw.inline_images(html, "https://mu2ewiki.fnal.gov", fetch)
    assert count == 2                                           # the same figure twice
    assert fetched == ["https://mu2ewiki.fnal.gov/images/a/ab/Plot.png",
                       "https://mu2ewiki.fnal.gov/images/c/cd/Huge.png"]   # once each; never the other host
    data_uri = "data:image/png;base64," + base64.b64encode(b"\x89PNG small").decode()
    assert out.count(data_uri) == 2 and "srcset" not in out and 'width="300"' not in out
    assert "https://elsewhere.org/logo.png" in out and "/images/c/cd/Huge.png" in out   # left as links


def test_a_failed_download_keeps_the_link():
    out, count = mw.inline_images('<img src="/images/a/ab/X.png">', "https://w", lambda url: None)
    assert count == 0 and "/images/a/ab/X.png" in out


# -- the source: auth, listing, dry run ---------------------------------------------

def test_auth_modes():
    assert mw.MediaWikiSource().auth == "kerberos"
    assert mw.MediaWikiSource(use_kerberos=False).auth == "none"
    assert mw.MediaWikiSource(auth="sso").auth == "sso"
    assert mw.MediaWikiSource.chunk_strategy == "section"
    with pytest.raises(ValueError):
        mw.MediaWikiSource(auth="basic")


def test_listing_pages_through_continuations(monkeypatch):
    src = mw.MediaWikiSource(auth="none", delay=0)
    answers = [
        {"query": {"pages": {"1": {"pageid": 1, "title": "A", "lastrevid": 11}}}, "continue": {"gapcontinue": "B", "continue": "gapcontinue||"}},
        {"query": {"pages": {"2": {"pageid": 2, "title": "B", "lastrevid": 22}}}},
    ]
    seen = []
    monkeypatch.setattr(src, "_api_request", lambda params: (seen.append(params), answers.pop(0))[1])
    pages = src.list_pages()
    assert [(p["page_id"], p["revision_id"]) for p in pages] == [(1, 11), (2, 22)]
    assert seen[0]["gapfilterredir"] == "nonredirects" and seen[1]["gapcontinue"] == "B"


def test_one_failed_request_makes_the_list_unusable(monkeypatch):
    src = mw.MediaWikiSource(auth="none", delay=0)
    answers = [{"query": {"pages": {"1": {"pageid": 1, "title": "A", "lastrevid": 1}}}, "continue": {"gapcontinue": "B"}}, None]
    monkeypatch.setattr(src, "_api_request", lambda params: answers.pop(0))
    assert src.list_pages() is None


def test_dry_run_changes_nothing(monkeypatch):
    src = mw.MediaWikiSource(auth="none")
    monkeypatch.setattr(src, "list_pages", lambda: [remote(1, "A", 2), remote(3, "C", 1)])
    monkeypatch.setattr(src, "_local_pages", lambda: [local("r1", 1, "A", 1)])
    for name in ("_add_page", "_update_page"):
        monkeypatch.setattr(src, name, lambda *a, **k: pytest.fail("a dry run must not write"))
    result = src.sync()
    assert result["applied"] is False and result["added"] == result["updated"] == 0
    assert len(result["plan"]["new"]) == 1 and len(result["plan"]["changed"]) == 1


# -- KB helpers a page update uses -------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from kb_mcp.kb.db_models import Base, Document, RawDocument, Source
    from kb_mcp.kb.embedding.db_models import Chunk

    engine = create_engine(f"sqlite:///{tmp_path}/wiki.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Source(id="mu2e-wiki"))
    s.add(RawDocument(id="raw-1", source_id="mu2e-wiki", doc_id="Computing", source_type="text/html", content_hash="h1"))
    s.add(RawDocument(id="raw-2", source_id="mu2e-wiki", doc_id="Other", source_type="text/html", content_hash="h2"))
    s.add(Document(id="text-1", source_id="mu2e-wiki", doc_id="Computing", raw_document_id="raw-1", doc_type="text", text="page",
                   source_type="text/html"))
    s.add(Document(id="img-1", source_id="mu2e-wiki", doc_id="Computing-img", raw_document_id="raw-1", doc_type="image",
                   text="a plot", parent_id="text-1", source_type="text/html"))
    s.add(Document(id="text-2", source_id="mu2e-wiki", doc_id="Other", raw_document_id="raw-2", doc_type="text", text="other",
                   source_type="text/html"))
    s.flush()
    yield SimpleNamespace(session=s, Document=Document, RawDocument=RawDocument, Chunk=Chunk)
    s.close()


def test_derived_documents_go_the_raw_document_stays(db):
    from kb_mcp.kb.documents.operations import delete_derived_documents

    assert delete_derived_documents("raw-1", session=db.session) == 2
    assert db.session.get(db.RawDocument, "raw-1") is not None
    assert {d.id for d in db.session.query(db.Document)} == {"text-2"}     # the other page untouched


@pytest.mark.parametrize("origin, held", [
    (None, True),                    # no visibility row yet
    ("privacy-hold", True),
    ("privacy-policy", True),        # the policy's decision was about the old content
    ("review", False),               # a person decided: kept
    ("cli", False),
])
def test_a_changed_page_is_held_unless_a_person_decided(monkeypatch, origin, held):
    from kb_mcp.kb import tools
    from kb_mcp.kb.documents import operations as ops

    current = None if origin is None else {"search": True, "get": True, "origin": origin}
    monkeypatch.setattr(tools, "_current_visibility", lambda raw, group: current)
    calls = []
    monkeypatch.setattr(ops, "_hold_for_privacy_scan", lambda raw, session: calls.append(raw))
    assert ops.hold_changed_document("raw-1", session=None) is held
    assert calls == (["raw-1"] if held else [])


# -- the hit header ----------------------------------------------------------------

def test_hits_show_when_the_source_last_edited_them(monkeypatch):
    from kb_mcp.privacy import redaction as r
    from kb_mcp.server.mcp import _format_search_results

    monkeypatch.setattr(r, "_load_rules", lambda: ({}, []))
    r.clear_cache()
    doc = SimpleNamespace(id="d", source_id="mu2e-wiki", doc_id="Computing", raw_document_id="raw-1", text="short page",
                          title="Computing", title_gen=None, uri="https://mu2ewiki.fnal.gov/wiki/Computing",
                          doc_type="text", source_type="text/html", meta={"wiki_last_edit": "2016-03-02T10:00:00+00:00"},
                          summary=None, update_time=datetime(2016, 3, 2, 10, 0))
    [out] = _format_search_results([{"document": doc, "chunks": []}])
    assert "LAST_EDITED: 2016-03-02" in out["text"]
    r.clear_cache()


def test_the_table_of_contents_is_removed_and_headings_stay():
    html = ('<div id="toc" class="toc"><div class="toctitle"><h2>Contents</h2></div><ul><li>1 Intro</li></ul></div>'
            '<h2><span class="mw-headline" id="Intro">Intro</span></h2><p>text</p>')
    out = mw._strip_toc(html)
    assert "Contents" not in out and "1 Intro" not in out
    assert "mw-headline" in out and "<p>text</p>" in out


def test_no_page_is_imported_when_image_descriptions_would_fail(monkeypatch):
    from kb_mcp.parser import image_descriptions

    src = mw.MediaWikiSource(auth="none")
    monkeypatch.setattr(src, "list_pages", lambda: [remote(1, "A", 1)])
    monkeypatch.setattr(src, "_local_pages", lambda: [])
    monkeypatch.setattr(src, "_add_page", lambda *a, **k: pytest.fail("must not import with a broken vision endpoint"))
    monkeypatch.setattr(image_descriptions, "vision_endpoint_problem", lambda: "gemma: AuthenticationError: 401")
    with pytest.raises(RuntimeError, match="nothing imported.*401"):
        src.sync(apply=True)


def test_vision_check_is_skipped_when_descriptions_are_off(monkeypatch):
    from kb_mcp.parser import image_descriptions

    monkeypatch.setenv("PARSE_IMAGE_LLM_DESCRIPTION", "false")
    assert image_descriptions.vision_endpoint_problem() is None


@pytest.mark.parametrize("doc_id, path", [
    ("TrackerIntro", "TrackerIntro"),
    ("ALCF:_Theta", "ALCF:_Theta"),
    ("Mock_Data_(MDS)", "Mock_Data_(MDS)"),
    ("Tracker/Calibration", "Tracker/Calibration"),
    ("Mu-e+", "Mu-e+"),
    ("What?_A_&_B_#1_100%", "What%3F_A_%26_B_%231_100%25"),
])
def test_page_urls_work_for_every_title(doc_id, path):
    assert mw.page_url("https://mu2ewiki.fnal.gov", doc_id) == f"https://mu2ewiki.fnal.gov/wiki/{path}"


def test_section_anchors_from_the_parse_api():
    sections = [{"line": "Introduction", "anchor": "Introduction"},
                {"line": "Mechanical <i>Construction</i>", "anchor": "Mechanical_Construction"},
                {"line": "Introduction", "anchor": "Introduction_2"},          # a repeated heading: first wins
                {"line": "", "anchor": "x"}]
    assert mw.section_anchors(sections) == {"Introduction": "Introduction",
                                            "Mechanical Construction": "Mechanical_Construction"}


def test_hits_link_to_the_sections_they_came_from():
    from kb_mcp.server.mcp import _section_links

    doc = SimpleNamespace(uri="https://mu2ewiki.fnal.gov/wiki/TrackerIntro",
                          meta={"section_anchors": {"Requirements": "Requirements", "Straw Assemblies": "Straw_Assemblies"}})
    chunks = [{"section_path": "TrackerIntro > Requirements"}, {"section_path": "TrackerIntro > Requirements"},
              {"section_path": ["TrackerIntro", "Straw Assemblies"]}, {"section_path": "TrackerIntro > Unknown"},
              {"section_path": None}]
    assert _section_links(doc, chunks) == (
        "Requirements: https://mu2ewiki.fnal.gov/wiki/TrackerIntro#Requirements | "
        "Straw Assemblies: https://mu2ewiki.fnal.gov/wiki/TrackerIntro#Straw_Assemblies")
    assert _section_links(SimpleNamespace(uri=doc.uri, meta={}), chunks) is None          # no anchors (DocDB)
    assert _section_links(SimpleNamespace(uri="file:///x", meta=doc.meta), chunks) is None  # no web address


def test_section_links_keep_anchors_readable():
    from kb_mcp.server.mcp import _section_links

    doc = SimpleNamespace(uri="https://w/wiki/Upload", meta={"section_anchors": {
        "Option 2, perform individual steps": "Option_2,_perform_individual_steps", "Rob's notes": "Rob's_notes"}})
    links = _section_links(doc, [{"section_path": "Upload > Option 2, perform individual steps"},
                                 {"section_path": "Upload > Rob's notes"}])
    assert "#Option_2,_perform_individual_steps" in links and "#Rob's_notes" in links


def test_a_sync_registers_its_source_before_the_first_page(monkeypatch):
    from kb_mcp.parser import image_descriptions

    src = mw.MediaWikiSource(auth="none", source_id="mu2e-internal-wiki")
    order = []
    monkeypatch.setattr(src, "list_pages", lambda: [remote(1, "A", 1)])
    monkeypatch.setattr(src, "_local_pages", lambda: [])
    monkeypatch.setattr(image_descriptions, "vision_endpoint_problem", lambda: None)
    monkeypatch.setattr(src, "_ensure_source", lambda: order.append("source"))
    monkeypatch.setattr(src, "_add_page", lambda page, out: order.append("page"))
    src.sync(apply=True, auto_embed=False, auto_summarize=False)
    assert order == ["source", "page"]
