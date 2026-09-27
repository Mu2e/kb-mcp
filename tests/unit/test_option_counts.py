"""Filter-dropdown counts come from the summary view unless a search is active."""

from kb_mcp.server.web.routes import api

COUNTS = [("mu2e-docdb", "text", 10), ("mu2e-docdb", "image", 5), ("mu2e-wiki", "text", 3)]


def test_counts_from_the_view_for_source_and_type_filters():
    assert api._option_count(COUNTS, {"source_id": "mu2e-docdb"}) == 15
    assert api._option_count(COUNTS, {"doc_type": "text"}) == 13
    assert api._option_count(COUNTS, {"source_id": "mu2e-docdb", "doc_type": "text"}) == 10
    assert api._option_count(COUNTS, {"source_id": "nothing"}) == 0


def test_a_text_search_or_no_view_counts_live(monkeypatch):
    calls = []
    monkeypatch.setattr("kb_mcp.kb.get_count", lambda filter_dict: calls.append(filter_dict) or 42)
    assert api._option_count(COUNTS, {"source_id": "mu2e-docdb", "text_contains": "x"}) == 42
    assert api._option_count(None, {"source_id": "mu2e-docdb"}) == 42
    assert len(calls) == 2
