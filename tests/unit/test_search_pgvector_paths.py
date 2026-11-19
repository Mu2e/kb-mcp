"""Which vector query `_search_pgvector` runs for a filtered search.

A filter that few or no embedded chunks pass used to reach the iterative
IVFFlat scan, which then read the whole index one scalar subquery per row:
measured 2026-09-30, a `source_id=mu2e-wiki` filter (no wiki chunk is
embedded) took 203 s and returned nothing. The embedded chunks that pass a
filter are now counted first, and the count picks the path:

  none                      -> no vector query at all
  up to SEARCH_EXACT_MAX_CHUNKS -> exact ranking of that subset
  more                      -> the index path, with a max_probes bound

Every semantic query also runs under SEARCH_VECTOR_TIMEOUT_MS. These tests
pin those choices without a database: a fake session records the SQL.
"""

import importlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pg = importlib.import_module("kb_mcp.kb.search.search_pgvector")


class FakeSession:
    """Records every statement; answers the count and the vector query."""

    def __init__(self, count):
        self.count = count
        self.sql = []
        self.params = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        self.params.append(params or {})
        if "count(*)" in sql:
            return SimpleNamespace(scalar=lambda: self.count)
        return SimpleNamespace(all=lambda: [], scalar=lambda: None)

    @contextmanager
    def begin_nested(self):
        yield

    def vector_queries(self):
        return [s for s in self.sql if "nearest AS" in s]

    def settings(self):
        return [s.strip() for s in self.sql if s.strip().startswith("SET LOCAL")]


TABLE = SimpleNamespace(name="embeddings_st_test")


@pytest.fixture(autouse=True)
def search_env(monkeypatch):
    monkeypatch.setenv("SEARCH_EXACT_MAX_CHUNKS", "1000")
    monkeypatch.setenv("SEARCH_IVFFLAT_MAX_PROBES", "256")
    monkeypatch.setenv("SEARCH_VECTOR_TIMEOUT_MS", "30000")


def run(session, **filters):
    return pg._search_pgvector(
        session, TABLE, [0.1, 0.2, 0.3], max_results=5, source_id=None,
        doc_type=None, chunking_strategy=None, **filters)


def test_no_filter_takes_the_index_path_without_counting():
    s = FakeSession(count=None)
    out = run(s)
    assert not [q for q in s.sql if "count(*)" in q]
    (query,) = s.vector_queries()
    assert "SELECT TRUE" in query and "filtered AS MATERIALIZED" not in query
    assert "SET LOCAL ivfflat.max_probes = 256" in s.settings()
    assert "SET LOCAL enable_seqscan = off" in s.settings()
    assert out["metadata"]["vector_path"] == "index"


def test_a_filter_no_chunk_passes_runs_no_vector_query():
    s = FakeSession(count=0)
    out = run(s, filter={"term": {"source_id": "mu2e-wiki"}})
    assert s.vector_queries() == []
    assert out["results"] == []
    assert out["metadata"]["vector_path"] == "no_match"
    assert out["metadata"]["filtered_chunks"] == 0


def test_a_narrow_filter_ranks_its_chunks_exactly():
    s = FakeSession(count=1000)
    out = run(s, filter={"term": {"source_id": "mu2e-wiki"}})
    (query,) = s.vector_queries()
    assert "filtered AS MATERIALIZED" in query and "SELECT TRUE" not in query
    assert not [x for x in s.settings() if "ivfflat" in x or "enable_seqscan" in x]
    assert out["metadata"]["vector_path"] == "exact"
    assert out["metadata"]["filtered_chunks"] == 1000


def test_a_broad_filter_takes_the_bounded_index_path():
    s = FakeSession(count=1001)
    out = run(s, filter={"term": {"doc_type": "text"}})
    (query,) = s.vector_queries()
    assert "SELECT TRUE" in query
    assert "SET LOCAL ivfflat.max_probes = 256" in s.settings()
    assert out["metadata"]["vector_path"] == "index"


def test_the_count_stops_one_past_the_exact_limit():
    s = FakeSession(count=5)
    run(s, filter={"term": {"source_id": "mu2e-wiki"}})
    (params,) = [p for q, p in zip(s.sql, s.params) if "count(*)" in q]
    assert params["count_cap"] == 1001


@pytest.mark.parametrize("count", [None, 0, 5, 5000])
def test_every_path_bounds_its_statements_and_then_lifts_the_bound(count):
    """The timeout comes before any statement that can be slow, and is
    lifted again: SET LOCAL would otherwise outlive this search and bound
    the full-text half of a hybrid search, which shares the transaction."""
    s = FakeSession(count=count)
    filters = {} if count is None else {"filter": {"term": {"source_id": "x"}}}
    run(s, **filters)
    stmts = [q.strip() for q in s.sql]
    assert stmts[0] == "SET LOCAL statement_timeout = 30000"
    assert stmts[-1] == "SET LOCAL statement_timeout = DEFAULT"


def test_a_zero_timeout_sets_no_bound(monkeypatch):
    monkeypatch.setenv("SEARCH_VECTOR_TIMEOUT_MS", "0")
    s = FakeSession(count=5)
    run(s, filter={"term": {"source_id": "x"}})
    assert not [q for q in s.sql if "statement_timeout" in q]


# --- the summary view: skip the live count for a coarse source/type filter ---
#
# Counting what `source_id=mu2e-docdb` or `doc_type=image` passes live took
# 14 s cold and over 45 s (2026-11-12). kb_chunk_counts answers a filter on
# source_id and/or doc_type alone; it is used only to skip the count when it
# says more than the exact limit, so a stale view cannot hide a match.


class ViewSession(FakeSession):
    """FakeSession that also answers the kb_chunk_counts query."""

    def __init__(self, count, view_total=None, view_error=False):
        super().__init__(count)
        self.view_total = view_total
        self.view_error = view_error

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "kb_chunk_counts" in sql:
            self.sql.append(sql)
            self.params.append(params or {})
            if self.view_error:
                raise RuntimeError('relation "kb_chunk_counts" does not exist')
            return SimpleNamespace(scalar=lambda: self.view_total)
        return super().execute(stmt, params)

    def view_queries(self):
        return [q for q in self.sql if "kb_chunk_counts" in q]

    def live_counts(self):
        return [q for q in self.sql if "count(*)" in q]


def test_a_broad_coarse_filter_skips_the_live_count():
    s = ViewSession(count=5, view_total=535_000)
    out = run(s, filter={"term": {"doc_type": "image"}})
    assert s.live_counts() == []
    (query,) = s.vector_queries()
    assert "SELECT TRUE" in query
    assert out["metadata"]["vector_path"] == "index"
    assert out["metadata"]["filtered_chunks"] == 535_000
    (params,) = [p for q, p in zip(s.sql, s.params) if "kb_chunk_counts" in q]
    assert params == {"doc_type": ["image"]}


def test_a_view_total_within_the_exact_limit_ranks_exactly_without_counting():
    s = ViewSession(count=5, view_total=163_000 // 1000)       # 163, limit 1000
    out = run(s, filter={"term": {"doc_type": "table"}})
    assert s.live_counts() == []
    (query,) = s.vector_queries()
    assert "filtered AS MATERIALIZED" in query
    assert out["metadata"]["vector_path"] == "exact"
    assert out["metadata"]["filtered_chunks"] == 163


def test_a_view_total_of_none_is_counted_live():
    """The view may lag an import, so "no chunks" is never taken on its word."""
    for view_total in (0, None):
        s = ViewSession(count=0, view_total=view_total)
        out = run(s, filter={"term": {"source_id": "mu2e-wiki"}})
        assert len(s.live_counts()) == 1
        assert s.vector_queries() == []
        assert out["metadata"]["vector_path"] == "no_match"


def test_a_missing_view_falls_back_to_the_live_count():
    s = ViewSession(count=5000, view_error=True)
    out = run(s, filter={"term": {"source_id": "mu2e-docdb"}})
    assert len(s.live_counts()) == 1
    assert out["metadata"]["vector_path"] == "index"


@pytest.mark.parametrize("filters", [
    {"filter": {"range": {"update_time": {"gte": "2026-10-01"}}}},
    {"filter": {"bool": {"must": [{"term": {"source_id": "x"}}, {"range": {"creating_time": {"gte": "2025"}}}]}}},
    {"filter": {"bool": {"must_not": [{"term": {"doc_type": "image"}}]}}},
    {"filter": {"term": {"docdb_id": "58830"}}},
    {"filter": {"wildcard": {"doc_id": "58716*"}}},
    {"filter": {"term": {"source_id": "x"}}, "author": "Simon"},
])
def test_any_other_filter_never_reads_the_view(filters):
    s = ViewSession(count=5, view_total=999_999)
    run(s, **filters)
    assert s.view_queries() == []
    assert len(s.live_counts()) == 1


def test_visibility_still_reads_the_view_and_filters_the_vector_query():
    """Every MCP search enforces visibility; it must not lose the shortcut."""
    s = ViewSession(count=5, view_total=535_000)
    out = run(s, filter={"term": {"doc_type": "image"}}, enforce_visibility=True)
    assert s.live_counts() == []
    assert out["metadata"]["vector_path"] == "index"
    (query,) = s.vector_queries()
    assert "visible_search" in query


def test_source_and_type_arguments_count_like_a_filter():
    s = ViewSession(count=5, view_total=999_999)
    pg._search_pgvector(
        s, TABLE, [0.1, 0.2, 0.3], max_results=5, source_id="mu2e-docdb",
        doc_type="text", chunking_strategy=None)
    assert s.live_counts() == []
    (params,) = [p for q, p in zip(s.sql, s.params) if "kb_chunk_counts" in q]
    assert params == {"source_id": ["mu2e-docdb"], "doc_type": ["text"]}


def test_a_chunking_strategy_needs_the_live_count():
    s = ViewSession(count=5, view_total=999_999)
    pg._search_pgvector(
        s, TABLE, [0.1, 0.2, 0.3], max_results=5, source_id="mu2e-docdb",
        doc_type=None, chunking_strategy="section_512")
    assert s.view_queries() == [] and len(s.live_counts()) == 1


@pytest.mark.parametrize("filter,expected", [
    (None, {}),
    ({"term": {"source_id": "a"}}, {"source_id": {"a"}}),
    ({"terms": {"source_id": ["a", "b"]}}, {"source_id": {"a", "b"}}),
    ({"bool": {"must": [{"term": {"source_id": "a"}}, {"term": {"doc_type": "text"}}]}},
     {"source_id": {"a"}, "doc_type": {"text"}}),
    ({"bool": {"must": {"term": {"doc_type": "text"}}}}, {"doc_type": {"text"}}),
    ({"bool": {"must": [{"terms": {"source_id": ["a", "b"]}}, {"term": {"source_id": "b"}}]}},
     {"source_id": {"b"}}),
    ({"bool": {"must": [{"term": {"source_id": "a"}}, {"term": {"source_id": "b"}}]}},
     {"source_id": set()}),
    ({"term": {"doc_id": "1"}}, None),
    ({"term": {"source_id": ["a"]}}, None),
    ({"bool": {"must": [{"term": {"source_id": "a"}}], "should": [{"term": {"doc_type": "text"}}]}}, None),
    ({"bool": {"must_not": [{"term": {"source_id": "a"}}]}}, None),
    ({"term": {"source_id": "a"}, "range": {"x": {"gte": 1}}}, None),
    ({"match": {"source_id": "a"}}, None),
])
def test_coarse_constraints(filter, expected):
    assert pg._coarse_constraints(filter) == expected


def _cte(query, name):
    """The body of one CTE of the vector query, up to the next CTE."""
    body = query.split(f"{name} AS (", 1)[1]
    return body.split("\n        ),", 1)[0]


@pytest.mark.parametrize("count", [None, 10])  # index path, exact path
def test_candidates_are_grouped_without_reading_chunk_rows(count):
    # Mapping a candidate to its document must need only chunks.id and
    # chunks.document_id, which idx_chunks_id_document_id answers from the
    # index; the other chunk columns are read for the returned chunks only.
    s = FakeSession(count=count)
    run(s, filter={"term": {"doc_type": "text"}} if count else None)
    (query,) = s.vector_queries()
    nearest = _cte(query, "nearest")
    assert "c.document_id" in nearest
    for column in ("chunk_index", "chunk_strategy", "char_start_index", "token_length", "section_path"):
        assert column not in nearest
    final = query.split("FROM diverse_chunks dc", 1)[1]
    assert "JOIN chunks c ON c.id = dc.chunk_id" in final


def test_hnsw_settings_ride_along_on_the_index_path(monkeypatch):
    monkeypatch.setenv("SEARCH_HNSW_EF_SEARCH", "123")
    monkeypatch.setenv("SEARCH_HNSW_MAX_SCAN_TUPLES", "4567")
    s = FakeSession(count=None)
    run(s)
    assert "SET LOCAL hnsw.ef_search = 123" in s.settings()
    assert "SET LOCAL hnsw.iterative_scan = relaxed_order" in s.settings()
    assert "SET LOCAL hnsw.max_scan_tuples = 4567" in s.settings()


def test_the_exact_path_sets_no_index_settings():
    s = FakeSession(count=10)
    run(s, filter={"term": {"doc_type": "text"}})
    assert not [x for x in s.settings() if "hnsw" in x or "ivfflat" in x]
