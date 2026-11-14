"""`ensure_search_indexes` builds the indexes the search needs on an existing database.

create_all() never adds an index to a table that exists, so `kb tools
db-maintain` builds them, CONCURRENTLY, and rebuilds one that an interrupted
concurrent build left invalid (it exists but the planner never uses it).
"""

from types import SimpleNamespace

from kb_mcp.kb import database
from kb_mcp.kb.embedding.db_models import Chunk


class FakeConn:
    def __init__(self, dialect="postgresql", may_create=True, valid=None):
        self.dialect = SimpleNamespace(name=dialect)
        self.may_create = may_create
        self.valid = valid          # None: missing, True: valid, False: invalid
        self.sql = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if "has_schema_privilege" in sql:
            return SimpleNamespace(scalar=lambda: self.may_create)
        if "indisvalid" in sql:
            return SimpleNamespace(scalar=lambda: self.valid)
        return SimpleNamespace(scalar=lambda: None)

    def ddl(self):
        return [s for s in self.sql if s.startswith(("CREATE", "DROP"))]


def test_a_missing_index_is_built_concurrently():
    conn = FakeConn(valid=None)
    assert database.ensure_search_indexes(conn) == ["idx_chunks_id_document_id"]
    assert conn.ddl() == [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_id_document_id ON chunks (id) INCLUDE (document_id)"]


def test_a_valid_index_is_left_alone():
    conn = FakeConn(valid=True)
    assert database.ensure_search_indexes(conn) == []
    assert conn.ddl() == []


def test_an_invalid_index_is_dropped_and_built_again():
    conn = FakeConn(valid=False)
    assert database.ensure_search_indexes(conn) == ["idx_chunks_id_document_id"]
    assert conn.ddl() == [
        "DROP INDEX CONCURRENTLY IF EXISTS idx_chunks_id_document_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_id_document_id ON chunks (id) INCLUDE (document_id)"]


def test_a_role_that_may_not_create_skips():
    conn = FakeConn(may_create=False)
    assert database.ensure_search_indexes(conn) == []
    assert conn.ddl() == []


def test_not_postgres_skips():
    conn = FakeConn(dialect="sqlite")
    assert database.ensure_search_indexes(conn) == []
    assert conn.sql == []


def test_the_chunk_model_declares_the_same_index_for_new_databases():
    (index,) = [i for i in Chunk.__table__.indexes if i.name == "idx_chunks_id_document_id"]
    assert [c.name for c in index.columns] == ["id"]
    assert index.dialect_options["postgresql"]["include"] == ["document_id"]
