"""optimize_embedding_index builds the IVFFlat index concurrently and swaps it in."""
from unittest.mock import MagicMock

import pytest

from kb_mcp.kb.embedding.embedding import _build_ivfflat_index


def _session(valid=True):
    """A session whose bind.connect() yields a recording connection."""
    conn = MagicMock()
    conn.execution_options.return_value = conn
    conn.__enter__.return_value = conn
    conn.execute.return_value.scalar.return_value = valid
    session = MagicMock()
    session.bind.connect.return_value = conn
    return session, conn


def _sql(conn):
    calls = [str(c.args[0]) for c in conn.execute.call_args_list]
    return calls + [c.args[0] for c in conn.exec_driver_sql.call_args_list]


def test_builds_concurrently_under_temp_name_then_swaps():
    session, conn = _session()
    _build_ivfflat_index(session, "emb_tbl", "emb_tbl_vector_idx", 906)
    sql = " | ".join(_sql(conn))
    assert "CREATE INDEX CONCURRENTLY" in sql and "emb_tbl_vector_idx_new" in sql
    assert "lists = 906" in sql
    assert 'ALTER INDEX "emb_tbl_vector_idx_new" RENAME TO "emb_tbl_vector_idx"' in sql
    assert [c.args[0] for c in conn.exec_driver_sql.call_args_list] == ["BEGIN", "COMMIT"]
    assert "ANALYZE" in sql


def test_raises_maintenance_work_mem_and_disables_timeout_first():
    session, conn = _session()
    _build_ivfflat_index(session, "t", "t_idx", 100, maintenance_work_mem="1GB")
    first = _sql(conn)[:2]
    assert "maintenance_work_mem = '1GB'" in first[0]
    assert "statement_timeout = 0" in first[1]


def test_invalid_index_keeps_old_index():
    session, conn = _session(valid=False)
    with pytest.raises(RuntimeError):
        _build_ivfflat_index(session, "t", "t_idx", 100)
    sql = " | ".join(_sql(conn))
    assert "RENAME TO" not in sql
    assert 'DROP INDEX IF EXISTS "t_idx"' not in sql  # old index never dropped


def test_under_hnsw_the_ivfflat_index_is_not_rebuilt(monkeypatch):
    """SEARCH_VECTOR_INDEX=hnsw (the default): optimize-index refuses instead of building 4 GB nobody reads."""
    from types import SimpleNamespace
    from kb_mcp.kb.embedding import embedding as emb

    monkeypatch.delenv("SEARCH_VECTOR_INDEX", raising=False)
    session = SimpleNamespace(bind=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
                              execute=lambda *a, **k: pytest.fail("must not touch the table"))
    monkeypatch.setattr(emb, "_get_embedding_table", lambda s, n: ({}, SimpleNamespace(name="embeddings_x"), "x"))
    monkeypatch.setattr(emb, "_build_ivfflat_index", lambda *a, **k: pytest.fail("must not build IVFFlat"))
    out = emb.optimize_embedding_index("x", session=session)
    assert out["index_rebuilt"] is False and "SEARCH_VECTOR_INDEX is hnsw" in out["message"]
