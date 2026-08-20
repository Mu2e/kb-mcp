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
    # structured parser output, split out from documents.parser_output
    "document_parser_outputs",
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
    assert "content_hash" in cols
    # Simon's raw-document linkage
    assert "raw_document_id" in cols
    # parser_output moved out of documents into its own table (see below) —
    # documents should NOT carry it as a column anymore.
    assert "parser_output" not in cols


def test_document_parser_outputs_table(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("document_parser_outputs")}
    assert "document_id" in cols
    assert "output" in cols


def test_chunks_provenance_lives_in_meta(engine):
    # bbox / body_self_refs / page_start / page_end used to be dedicated
    # columns; they're opaque write-once provenance folded into the
    # existing general-purpose `meta` JSONB column instead.
    cols = {c["name"] for c in inspect(engine).get_columns("chunks")}
    assert "meta" in cols
    for col in ("page_start", "page_end", "bbox", "body_self_refs"):
        assert col not in cols, col


def test_ensure_column_patchers_are_idempotent(engine):
    # Both patchers are currently no-ops (their column lists are empty —
    # see database.py) but must stay safe to call repeatedly.
    for _ in range(2):
        _ensure_documents_columns(engine)
        _ensure_chunks_columns(engine)
