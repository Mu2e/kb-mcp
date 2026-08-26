"""End-to-end checks that Elasticsearch-style search filters actually filter.

The unit tests in tests/unit/test_search_filters.py pin the SQL/param contract
without a database. These run the real query against the configured PostgreSQL
knowledge base, because the bug they guard against was invisible at the unit
level in one specific way: search_hybrid catches a failing branch and returns
"No results found", so a filter that crashed the query looked exactly like a
filter that legitimately matched nothing.

Skipped unless a PostgreSQL knowledge base with indexed documents is reachable.
"""

import pytest

from kb_mcp.kb import get_db_session
from kb_mcp.kb.db_models import Document


@pytest.fixture(scope="module")
def corpus():
    """A source_id and doc_type that actually exist, or skip."""
    try:
        with get_db_session() as session:
            row = (
                session.query(Document.source_id, Document.doc_type)
                .filter(Document.text.isnot(None))
                .first()
            )
            if row is None:
                pytest.skip("knowledge base has no documents")
            total = session.query(Document).count()
            return {"source_id": row[0], "doc_type": row[1], "total": total}
    except Exception as e:  # noqa: BLE001 - any connection problem means skip
        pytest.skip(f"knowledge base not reachable: {e}")


@pytest.fixture(scope="module")
def query(corpus):
    """A query that returns results unfiltered, or skip."""
    from kb_mcp.kb import search

    for q in ("tracker", "calibration", "detector", "the"):
        if search(q, max_results=5).get("results"):
            return q
    pytest.skip("no query returned results; cannot test filtering")


def _n(response):
    return len(response.get("results", []))


def test_unfiltered_search_returns_results(query):
    """Baseline - without this the rest proves nothing."""
    from kb_mcp.kb import search

    assert _n(search(query, max_results=5)) > 0


def test_direct_column_filter_returns_results(query, corpus):
    """The regression: {"term": {"source_id": ...}} used to crash and report 0."""
    from kb_mcp.kb import search

    response = search(
        query, max_results=5,
        filter={"term": {"source_id": corpus["source_id"]}},
    )
    assert _n(response) > 0, "a filter on an existing source_id returned nothing"


def test_filter_actually_restricts_to_the_requested_source(query, corpus):
    """A filter that returns rows but ignores the predicate is no better."""
    from kb_mcp.kb import search

    response = search(
        query, max_results=10,
        filter={"term": {"source_id": corpus["source_id"]}},
    )
    sources = {r["document"].source_id for r in response.get("results", [])}
    assert sources <= {corpus["source_id"]}, f"filter leaked other sources: {sources}"


def test_nonexistent_value_returns_nothing(query):
    """The complement: a filter that should match nothing must match nothing."""
    from kb_mcp.kb import search

    response = search(
        query, max_results=5,
        filter={"term": {"source_id": "no-such-source-xyz"}},
    )
    assert _n(response) == 0


def test_terms_filter_matches_any_listed_value(query, corpus):
    from kb_mcp.kb import search

    response = search(
        query, max_results=10,
        filter={"terms": {"source_id": [corpus["source_id"], "no-such-source-xyz"]}},
    )
    assert _n(response) > 0
    sources = {r["document"].source_id for r in response.get("results", [])}
    assert sources <= {corpus["source_id"]}


def test_bool_must_combines_predicates(query, corpus):
    from kb_mcp.kb import search

    response = search(
        query, max_results=10,
        filter={"bool": {"must": [
            {"term": {"source_id": corpus["source_id"]}},
            {"term": {"doc_type": corpus["doc_type"]}},
        ]}},
    )
    for r in response.get("results", []):
        assert r["document"].source_id == corpus["source_id"]
        assert r["document"].doc_type == corpus["doc_type"]


def test_bool_must_not_excludes(query, corpus):
    from kb_mcp.kb import search

    response = search(
        query, max_results=10,
        filter={"bool": {"must_not": [{"term": {"source_id": corpus["source_id"]}}]}},
    )
    sources = {r["document"].source_id for r in response.get("results", [])}
    assert corpus["source_id"] not in sources


def test_filter_does_not_silently_swallow_errors(query, corpus):
    """A crashing filter must not masquerade as an empty result set.

    Semantic search raises on a bad fragment rather than catching it, so this
    is the layer where a regression surfaces as an exception instead of a
    plausible-looking zero.
    """
    from kb_mcp.kb.search import search_semantic

    response = search_semantic(
        query, max_results=5,
        filter={"term": {"source_id": corpus["source_id"]}},
    )
    assert _n(response) > 0


@pytest.mark.parametrize("filter_dict", [
    {"match": {"title": "a"}},
    {"wildcard": {"title": "*a*"}},
    {"range": {"insert_time": {"gte": "2000-01-01"}}},
])
def test_other_query_shapes_execute_without_error(query, filter_dict):
    """These may legitimately match nothing; they must not raise."""
    from kb_mcp.kb.search import search_semantic

    search_semantic(query, max_results=3, filter=filter_dict)
