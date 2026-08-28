"""Regression tests for the Elasticsearch-style filter -> SQL fragment builder.

`get_filters_pgvector` compiles a filter dict to a WHERE fragment that
`search_pgvector` and `search_fulltext` splice into raw `text()` queries.
`text()` binds `:name` placeholders; the psycopg2 dialect's default paramstyle
renders `%(name)s`. Compiling with the default therefore produced a fragment
whose placeholders text() did not recognise, which reached PostgreSQL verbatim:

    psycopg2.errors.SyntaxError: syntax error at or near "%"
    LINE 21:   WHERE (d.meta ->> %(meta_1)s) = %(param_1)s

The two conversion passes meant to fix this up were both dead code: the first
counted literal '%s' occurrences, which `%(meta_1)s` does not contain, and the
second looked for `:name`, which the default paramstyle never emits. So the
params dict came back empty as well.

Every filtered search failed this way, and search_hybrid swallowed the error
per-branch and reported "No results found" - a broken filter was indistinguishable
from one that legitimately matched nothing.

These tests need no database: they pin the generated SQL/param contract, and
prove the fragment actually binds by compiling it with literal_binds.
"""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import aliased

from kb_mcp.kb.db_models import Document
from kb_mcp.kb.search.filters import (
    DIRECT_COLUMNS,
    build_where_clause,
    get_filters_pgvector,
)


@pytest.fixture
def doc_alias():
    """Matches the 'd' alias used by the raw queries in search_pgvector."""
    return aliased(Document, name="d")


def _bind(sql, params):
    """Splice a fragment into a text() query the way the real callers do.

    Compiling with literal_binds forces every placeholder to resolve, so an
    unbound or misnamed parameter raises instead of silently surviving.
    """
    stmt = text(f"SELECT 1 FROM documents d WHERE {sql}").bindparams(**params)
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


# --- the core regression -------------------------------------------------

def test_fragment_uses_text_bind_style_not_pyformat(doc_alias):
    """The exact defect: pyformat placeholders text() cannot bind."""
    sql, params = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}})

    assert "%(" not in sql, f"pyformat placeholder leaked into fragment: {sql}"
    assert "%s" not in sql
    assert ":filter_" in sql


def test_params_are_not_empty(doc_alias):
    """The dead conversion passes also returned an empty params dict."""
    sql, params = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}})

    assert params, "no bind values returned; placeholders would be unbound"
    assert set(params.values()) == {"parser", "docling"}


def test_fragment_binds_cleanly(doc_alias):
    """End-to-end proof: every placeholder resolves inside a text() query."""
    sql, params = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}})

    rendered = _bind(sql, params)
    assert "'parser'" in rendered and "'docling'" in rendered
    assert "%" not in rendered


@pytest.mark.parametrize(
    "filter_dict",
    [
        {"term": {"parser": "docling"}},
        {"term": {"source_id": "mu2e-docdb"}},
        {"terms": {"source_id": ["mu2e-docdb", "mu2e-wiki"]}},
        {"terms": {"topics": ["tracker", "calorimeter"]}},
        {"range": {"created": {"gte": "2023-01-01", "lte": "2024-12-31"}}},
        {"match": {"abstract": "tracker"}},
        {"wildcard": {"authors": "*Mu2e*"}},
        {"bool": {"must": [
            {"term": {"source_id": "mu2e-docdb"}},
            {"term": {"doc_type": "table"}},
        ]}},
        {"bool": {"should": [
            {"term": {"source_id": "mu2e-docdb"}},
            {"term": {"source_id": "mu2e-wiki"}},
        ]}},
        {"bool": {"must_not": [{"term": {"doc_type": "image"}}]}},
        {"bool": {
            "must": [{"term": {"source_id": "mu2e-docdb"}}],
            "must_not": [{"term": {"doc_type": "image"}}],
            "should": [{"term": {"parser": "docling"}}, {"term": {"parser": "marker"}}],
        }},
    ],
)
def test_every_supported_query_shape_binds(doc_alias, filter_dict):
    """Each Elasticsearch construct we advertise must produce bindable SQL."""
    sql, params = get_filters_pgvector(doc_alias, filter_dict)

    assert sql, f"no fragment produced for {filter_dict}"
    assert "%(" not in sql
    _bind(sql, params)  # raises if any placeholder is unbound


# --- parameter naming ----------------------------------------------------

def test_param_names_cannot_collide_with_outer_query(doc_alias):
    """The outer text() queries bind :max_results, :source_id and friends."""
    sql, params = get_filters_pgvector(doc_alias, {"term": {"source_id": "mu2e-docdb"}})

    outer = {"query_embedding", "initial_limit", "max_chunks_per_doc",
             "max_results", "source_id", "doc_type", "chunking_strategy", "parser_id"}
    assert not (set(params) & outer)
    assert all(name.startswith("filter_") for name in params)


def test_param_counter_offsets_names(doc_alias):
    """Lets a caller combine fragments without clashing."""
    _, a = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}}, param_counter=0)
    _, b = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}}, param_counter=10)

    assert set(a).isdisjoint(set(b))
    assert all(int(n.rsplit("_", 1)[1]) >= 10 for n in b)


def test_renaming_is_single_pass(doc_alias):
    """A bind renamed to filter_N must not be renamed again by a later pass."""
    # Many binds, so the rename map is large enough for a sequential
    # implementation to trip over its own output.
    filter_dict = {"terms": {"topics": [f"topic-{i}" for i in range(12)]}}
    sql, params = get_filters_pgvector(doc_alias, filter_dict)

    names = sorted(int(n.rsplit("_", 1)[1]) for n in params)
    assert names == list(range(len(names))), "bind names are not a clean sequence"
    # Whole-token count: a plain substring count would see :filter_1 inside
    # :filter_10, which is exactly the ambiguity the rename has to survive.
    for name in params:
        occurrences = re.findall(rf":{name}\b", sql)
        assert len(occurrences) == 1, f"{name} appears {len(occurrences)}x"
    _bind(sql, params)


def test_direct_column_values_keep_their_type(doc_alias):
    """Direct columns must not be str()-cast; timestamps need real types."""
    from datetime import datetime

    when = datetime(2024, 1, 1)
    sql, params = get_filters_pgvector(doc_alias, {"range": {"insert_time": {"gte": when}}})

    assert when in params.values(), f"datetime was coerced: {params}"


# --- direct columns vs metadata keys -------------------------------------

def test_direct_columns_target_real_columns(doc_alias):
    """source_id is a column, so it must not be looked up inside meta."""
    sql, _ = get_filters_pgvector(doc_alias, {"term": {"source_id": "mu2e-docdb"}})

    assert "d.source_id" in sql
    assert "meta" not in sql


def test_unknown_fields_fall_through_to_metadata(doc_alias):
    """Anything outside DIRECT_COLUMNS is a key inside the JSON meta blob."""
    sql, _ = get_filters_pgvector(doc_alias, {"term": {"parser": "docling"}})

    assert "meta ->>" in sql
    assert "parser" not in sql, "field name should be a bind value, not inlined SQL"


def test_documented_direct_columns_are_real_model_attributes():
    """DIRECT_COLUMNS is an allowlist; a typo there would silently hit meta."""
    for field in DIRECT_COLUMNS:
        assert hasattr(Document, field), f"{field} is not a Document column"


# --- integration with build_where_clause ---------------------------------

def test_build_where_clause_merges_filter_with_plain_kwargs(doc_alias):
    """The fragment has to coexist with the caller's own :name parameters."""
    sql, params = build_where_clause(
        source_id="mu2e-docdb",
        filter={"term": {"parser": "docling"}},
        skip_kwargs={"session"},
    )

    assert ":source_id" in sql and params["source_id"] == "mu2e-docdb"
    assert "%(" not in sql
    _bind(sql, params)


def test_build_where_clause_without_filter_is_unaffected(doc_alias):
    sql, params = build_where_clause(source_id="mu2e-docdb", doc_type="table")

    assert "%(" not in sql
    _bind(sql, params)


# --- documented deviations from Elasticsearch ----------------------------
#
# These are subset semantics, not bugs, but they are surprising enough that
# the module docstring and the kb_search tool description both call them out.
# Pin them so the docs cannot drift away from the behaviour.

def test_match_is_substring_not_analysed_fulltext(doc_alias):
    """Elasticsearch would analyse this; we emit LIKE '%...%'."""
    sql, params = get_filters_pgvector(doc_alias, {"match": {"abstract": "quick fox"}})

    assert "LIKE" in sql.upper()
    assert "%quick fox%" in params.values()


def test_metadata_range_compares_as_text(doc_alias):
    """meta ->> yields text, so numeric ranges sort lexicographically."""
    sql, _ = get_filters_pgvector(doc_alias, {"range": {"version": {"gte": 9}}})

    assert "meta ->>" in sql, "metadata range should go through the text accessor"


def test_unsupported_elasticsearch_queries_raise(doc_alias):
    """They must not silently match nothing."""
    for unsupported in ({"match_phrase": {"title": "x"}},
                        {"exists": {"field": "title"}},
                        {"prefix": {"title": "x"}},
                        {"query_string": {"query": "x"}}):
        with pytest.raises(ValueError, match="Unknown filter type"):
            get_filters_pgvector(doc_alias, unsupported)


def test_malformed_queries_raise(doc_alias):
    with pytest.raises(ValueError, match="exactly one field"):
        get_filters_pgvector(doc_alias, {"term": {"a": 1, "b": 2}})
    with pytest.raises(ValueError, match="must be a list"):
        get_filters_pgvector(doc_alias, {"terms": {"a": "not-a-list"}})


# --- kb_search surfaces filter errors instead of an empty result ---------

def test_kb_search_reports_invalid_filter_as_error():
    """search_hybrid swallows a failing branch as "No results found", so the
    MCP layer validates up front - otherwise a bad filter is indistinguishable
    from an empty result and the caller cannot tell it wrote nonsense."""
    import json
    from kb_mcp.server.mcp import kb_search

    result = json.loads(kb_search("tracker", search_filter={"match_phrase": {"title": "x"}}))

    assert "error" in result, f"invalid filter was not reported: {result}"
    assert "Invalid search_filter" in result["error"]
    assert "Supported:" in result["error"], "error should name the supported queries"


def test_kb_search_reports_malformed_filter_as_error():
    import json
    from kb_mcp.server.mcp import kb_search

    result = json.loads(kb_search("tracker", search_filter={"term": {"a": 1, "b": 2}}))

    assert "error" in result
    assert "exactly one field" in result["error"]
