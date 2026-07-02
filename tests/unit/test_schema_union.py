"""Schema-union test: fresh-DB creation must yield BOTH sides' tables/columns,
and the hand-rolled column-drift patchers must be idempotent.

Mirrors init_db()'s model registration (imports every model module) but runs
against a private on-disk SQLite engine so it never touches configured DBs.
"""

import pytest
from sqlalchemy import create_engine, inspect

from kb_mcp.kb.db_models import Base
# Model modules must be imported so their tables register with Base.metadata
# (mirrors the imports inside kb.database.init_db).
from kb_mcp.kb.embedding.db_models import Chunk, EmbeddingConfig  # noqa: F401
from kb_mcp.kb.search.db_models import SearchLog  # noqa: F401
from kb_mcp.kb.eval.db_models import (  # noqa: F401
    EvalGeneration, EvalDataset, EvalAudit, EvalRun, EvalResult,
    EvalRetrievedDocument,
)
from kb_mcp.kb.graph.db_models import (  # noqa: F401
    GraphNodeType, GraphVerb, GraphNode, GraphRelation,
    GraphRelationEvidence, GraphNodeMap, GraphExtractionLog,
)
from kb_mcp.kb.db_models import (  # noqa: F401
    ParserComparison, ParserCategories, PrivacyFilter, RawDocument,
)
from kb_mcp.kb.database import _ensure_documents_columns, _ensure_chunks_columns


EXPECTED_TABLES = {
    # base
    "sources", "documents", "parsers", "chunks",
    # Simon's side
    "documents_raw", "privacy_filters", "parser_comparisons", "parser_categories",
    # eval (shared / extended by both sides)
    "eval_generation", "eval_dataset", "eval_audit", "eval_runs",
    "eval_results", "eval_retrieved_documents",
    # graph
    "graph_nodes", "graph_relations",
}


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/schema_union.db")
    Base.metadata.create_all(eng)
    return eng


def test_union_schema_tables_exist(engine):
    tables = set(inspect(engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {sorted(missing)}"


def test_documents_has_both_sides_columns(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("documents")}
    # Sam's structured-parsing column (parser-agnostic raw parser output)
    assert "parser_output" in cols
    assert "content_hash" in cols
    # Simon's raw-document linkage
    assert "raw_document_id" in cols


def test_chunks_have_provenance_columns(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("chunks")}
    for col in ("page_start", "page_end", "bbox", "body_self_refs"):
        assert col in cols, col


def test_ensure_column_patchers_are_idempotent(engine):
    # Fresh create_all already has the columns; the patchers must be no-ops
    # that don't raise — and stay safe when run repeatedly.
    for _ in range(2):
        _ensure_documents_columns(engine)
        _ensure_chunks_columns(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("documents")}
    assert "parser_output" in cols


def test_ensure_column_patchers_add_missing_columns(tmp_path):
    """Simulate pre-migration tables: drop the new columns, re-run patchers."""
    from sqlalchemy import text

    eng = create_engine(f"sqlite:///{tmp_path}/drift.db")
    Base.metadata.create_all(eng)
    with eng.connect() as conn:
        conn.execute(text("ALTER TABLE documents DROP COLUMN parser_output"))
        conn.execute(text("ALTER TABLE chunks DROP COLUMN page_start"))
        conn.commit()

    _ensure_documents_columns(eng)
    _ensure_chunks_columns(eng)

    insp = inspect(eng)
    assert "parser_output" in {c["name"] for c in insp.get_columns("documents")}
    assert "page_start" in {c["name"] for c in insp.get_columns("chunks")}
