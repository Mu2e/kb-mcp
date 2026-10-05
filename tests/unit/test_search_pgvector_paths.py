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
