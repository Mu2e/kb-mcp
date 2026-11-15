"""kb embedding hnsw-index: build HNSW concurrently, drop either vector index safely.

HNSW is opt-in: once a valid HNSW index exists the planner uses it for every
index-path search, so nothing builds it unless asked. These tests pin the SQL
with a recording connection (no database).
"""
from unittest.mock import MagicMock

import pytest

from kb_mcp.kb.embedding.embedding import _build_hnsw_index, _drop_vector_index, hnsw_index_name


def _session(valid_by_name):
    """A session whose connection answers indisvalid per index name (None: no index)."""
    conn = MagicMock()
    conn.execution_options.return_value = conn
    conn.__enter__.return_value = conn

    def execute(stmt, params=None):
        result = MagicMock()
        if params and "n" in params:
            name = params["n"].strip('"')
            value = valid_by_name(name) if callable(valid_by_name) else valid_by_name.get(name)
            result.scalar.return_value = value
        return result

    conn.execute.side_effect = execute
    bind = MagicMock()
    bind.connect.return_value = conn
    return bind, conn


def _sql(conn):
    return [str(c.args[0]) for c in conn.execute.call_args_list if "indisvalid" not in str(c.args[0])]


def test_builds_concurrently_with_the_tested_settings_and_raises_memory_first():
    built = {"done": False}

    def valid(name):  # missing before the build, valid after it
        return True if built["done"] else None

    session, conn = _session(valid)
    orig = conn.execute.side_effect

    def execute(stmt, params=None):
        if "CREATE INDEX CONCURRENTLY" in str(stmt):
            built["done"] = True
        return orig(stmt, params)

    conn.execute.side_effect = execute
    assert _build_hnsw_index(session, "emb", "emb_hnsw_idx", parallel_workers=4) is True
    sql = _sql(conn)
    assert "maintenance_work_mem = '6GB'" in sql[0] and "statement_timeout = 0" in sql[1]
    assert "max_parallel_maintenance_workers = 4" in sql[2]
    create = [s for s in sql if "CREATE INDEX" in s][0]
    assert 'CONCURRENTLY "emb_hnsw_idx"' in create and "USING hnsw (embedding vector_cosine_ops)" in create
    assert "m = 16" in create and "ef_construction = 64" in create
    assert any(s.startswith('ANALYZE "emb"') for s in sql)


def test_a_valid_hnsw_index_is_not_rebuilt():
    session, conn = _session({"emb_hnsw_idx": True})
    assert _build_hnsw_index(session, "emb", "emb_hnsw_idx") is False
    assert not [s for s in _sql(conn) if "CREATE" in s or "DROP" in s]


def test_an_invalid_leftover_is_dropped_before_building():
    state = {"n": 0}

    def valid(name):  # invalid leftover, then valid after the new build
        state["n"] += 1
        return False if state["n"] == 1 else True

    session, conn = _session(valid)
    _build_hnsw_index(session, "emb", "emb_hnsw_idx")
    ddl = [s.strip().split("\n")[0] for s in _sql(conn) if "DROP" in s or "CREATE" in s]
    assert ddl[0] == 'DROP INDEX CONCURRENTLY IF EXISTS "emb_hnsw_idx"'
    assert ddl[1].startswith("CREATE INDEX CONCURRENTLY")


def test_an_invalid_new_build_is_dropped_and_raises():
    session, conn = _session(lambda name: None)  # never becomes valid
    with pytest.raises(RuntimeError):
        _build_hnsw_index(session, "emb", "emb_hnsw_idx")
    assert _sql(conn)[-1] == 'DROP INDEX CONCURRENTLY IF EXISTS "emb_hnsw_idx"'


def test_dropping_hnsw_goes_back_to_ivfflat():
    session, conn = _session({"emb_hnsw_idx": True, "emb_vector_idx": True})
    assert "Dropped hnsw" in _drop_vector_index(session, "emb", "hnsw")
    assert _sql(conn) == ['DROP INDEX CONCURRENTLY IF EXISTS "emb_hnsw_idx"']


def test_ivfflat_is_dropped_only_while_a_valid_hnsw_index_exists():
    session, conn = _session({"emb_vector_idx": True, "emb_hnsw_idx": None})
    with pytest.raises(RuntimeError):
        _drop_vector_index(session, "emb", "ivfflat")
    assert _sql(conn) == []

    session, conn = _session({"emb_vector_idx": True, "emb_hnsw_idx": True})
    assert "Dropped ivfflat" in _drop_vector_index(session, "emb", "ivfflat")
    assert _sql(conn) == ['DROP INDEX CONCURRENTLY IF EXISTS "emb_vector_idx"']


def test_dropping_a_missing_index_is_a_no_op():
    session, conn = _session({})
    assert "No hnsw index" in _drop_vector_index(session, "emb", "hnsw")
    assert _sql(conn) == []


def test_index_name():
    assert hnsw_index_name("embeddings_st_bgesmallenv1_5") == "embeddings_st_bgesmallenv1_5_hnsw_idx"


def test_hnsw_is_the_default_vector_index(monkeypatch):
    from kb_mcp.config import get_search_config

    monkeypatch.delenv("SEARCH_VECTOR_INDEX", raising=False)
    assert get_search_config()["vector_index"] == "hnsw"
    monkeypatch.setenv("SEARCH_VECTOR_INDEX", "IVFFlat")
    assert get_search_config()["vector_index"] == "ivfflat"


def test_an_unknown_vector_index_warns_instead_of_breaking_searches(monkeypatch):
    from kb_mcp.config import get_search_config

    monkeypatch.setenv("SEARCH_VECTOR_INDEX", "hnws")
    with pytest.warns(UserWarning, match="SEARCH_VECTOR_INDEX"):
        assert get_search_config()["vector_index"] == "hnsw"
