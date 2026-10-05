"""enforce_visibility SQL wiring in the two search backends, no database needed.

A fake session records every statement; the fake execute always returns zero
rows, which both _search_pgvector and search_fulltext treat as "no matches"
and return early on -- before touching session.query(Document), which the
fake does not implement. That's enough surface to prove the right SQL was
built; the guard actually excluding a hidden document at runtime is proven
live (real positive-control query, built from the target document's own
chunk text) in this session's manual verification, same as
test_document_visibility_upsert_sql.py does for the upsert.

The two backends join differently on purpose (see search_pgvector.py's own
comments): search_fulltext uses a plain LEFT JOIN (a normal GIN index scan,
insensitive to join shape); _search_pgvector folds the join into its
existing per-row scalar subquery instead of joining at the outer query
level, preserving the index-friendly shape its ANN scan depends on.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kb_mcp.kb.search.search_pgvector import _search_pgvector
from kb_mcp.kb.search.search_fulltext import search_fulltext


class FakeSession:
    """Records every statement; answers every execute() with zero rows."""

    def __init__(self):
        self.sql = []
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        return SimpleNamespace(all=lambda: [], scalar=lambda: None, rowcount=0)

    @contextmanager
    def begin_nested(self):
        yield

    def query(self, *a, **kw):  # pragma: no cover - should never be reached
        raise AssertionError("query() reached despite the zero-row early return")

    def main_queries(self, marker):
        return [s for s in self.sql if marker in s]


@pytest.fixture(autouse=True)
def search_env(monkeypatch):
    monkeypatch.setenv("SEARCH_IVFFLAT_PROBES", "128")
    monkeypatch.setenv("SEARCH_MAX_CHUNKS_PER_DOC", "10")
    monkeypatch.setenv("SEARCH_INITIAL_LIMIT_MULTIPLIER", "50")


TABLE = SimpleNamespace(name="embeddings_st_test")


# --- _search_pgvector ------------------------------------------------------

def _run_pgvector(session, enforce_visibility):
    return _search_pgvector(
        session, TABLE, [0.1, 0.2, 0.3], max_results=5, source_id=None,
        doc_type=None, chunking_strategy=None, enforce_visibility=enforce_visibility,
    )


def test_pgvector_default_has_no_visibility_join():
    s = FakeSession()
    _run_pgvector(s, enforce_visibility=False)
    (query,) = s.main_queries("nearest AS")
    assert "document_visibility_current" not in query


def test_pgvector_enforced_joins_inside_the_scalar_subquery_not_outside():
    s = FakeSession()
    _run_pgvector(s, enforce_visibility=True)
    (query,) = s.main_queries("nearest AS")
    assert "LEFT JOIN document_visibility_current dvc" in query
    assert "COALESCE(dvc.visible_search, TRUE)" in query
    # Still inside the per-row "SELECT TRUE ... LIMIT 1" subquery, not a
    # second top-level join on the knn CTE -- that would change the plan
    # shape the index scan depends on (see search_pgvector.py's own comment
    # on why filters are a scalar subquery, not a join).
    subquery_start = query.index("SELECT TRUE")
    join_pos = query.index("LEFT JOIN document_visibility_current")
    limit_1_pos = query.index("LIMIT 1")
    assert subquery_start < join_pos < limit_1_pos


def test_pgvector_join_scopes_to_the_default_group():
    s = FakeSession()
    _run_pgvector(s, enforce_visibility=True)
    (query,) = s.main_queries("nearest AS")
    assert "dvc.group_name = '*'" in query


# --- search_fulltext ---------------------------------------------------

def _run_fulltext(session, enforce_visibility):
    return search_fulltext(
        "muon beam", max_results=5, session=session, enforce_visibility=enforce_visibility,
    )


def test_fulltext_default_has_no_visibility_join():
    s = FakeSession()
    _run_fulltext(s, enforce_visibility=False)
    (query,) = s.main_queries("base_candidates AS")
    assert "document_visibility_current" not in query


def test_fulltext_enforced_adds_a_plain_left_join():
    s = FakeSession()
    _run_fulltext(s, enforce_visibility=True)
    (query,) = s.main_queries("base_candidates AS")
    assert "LEFT JOIN document_visibility_current dvc" in query
    assert "COALESCE(dvc.visible_search, TRUE)" in query


def test_fulltext_join_scopes_to_the_default_group():
    s = FakeSession()
    _run_fulltext(s, enforce_visibility=True)
    (query,) = s.main_queries("base_candidates AS")
    assert "dvc.group_name = '*'" in query


def test_fulltext_join_keys_on_raw_document_id_not_document_id():
    """Visibility is a raw-file-level decision (one row covers every parser's
    output of that file), not a per-parsed-Document one."""
    s = FakeSession()
    _run_fulltext(s, enforce_visibility=True)
    (query,) = s.main_queries("base_candidates AS")
    assert "dvc.raw_document_id = d.raw_document_id" in query
