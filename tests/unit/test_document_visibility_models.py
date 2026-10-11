"""Schema and default-value checks for the document-visibility tables.

document_visibility_decisions is an append-only log (one row per decision,
never updated); document_visibility_current is the upserted, indexed
resolution of "latest decision per (raw_document_id, group_name)" that
search/get actually query; document_visibility_runs mirrors ImportRun's
run-level accounting for a future sweep tool. See each class's docstring in
kb_mcp.kb.db_models for the full design rationale.

document_visibility_decisions.seq relies on a real Postgres SEQUENCE
(nextval()) that SQLite cannot emit, so row-level behavior for that table is
covered separately in test_document_visibility_postgres.py, gated on a real
Postgres connection. Everything here runs against ephemeral SQLite: table
shape, and the two tables whose columns are plain client-side defaults
(document_visibility_current, document_visibility_runs).
"""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import (
    Base,
    DOCUMENT_VISIBILITY_DEFAULT_GROUP,
    DocumentVisibilityCurrent,
    DocumentVisibilityRun,
)


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/document_visibility.db")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_default_group_sentinel_is_not_none():
    # NULL behaves inconsistently in the unique index / composite key that
    # document_visibility_current's upsert relies on.
    assert DOCUMENT_VISIBILITY_DEFAULT_GROUP == "*"
    assert DOCUMENT_VISIBILITY_DEFAULT_GROUP is not None


def test_decisions_table_has_a_seq_separate_from_the_uuid_pk(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("document_visibility_decisions")}
    assert "id" in cols  # UUID primary key -- not sortable
    assert "seq" in cols  # DB-assigned monotonic ordering key


def test_current_tables_primary_key_is_the_group_scoped_composite(engine):
    pk = inspect(engine).get_pk_constraint("document_visibility_current")
    assert set(pk["constrained_columns"]) == {"raw_document_id", "group_name"}


def test_current_mirrors_the_winning_decisions_seq(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("document_visibility_current")}
    assert "decision_seq" in cols
    assert "decision_id" in cols


def test_run_table_mirrors_import_runs_accounting_shape(engine):
    cols = {c["name"] for c in inspect(engine).get_columns("document_visibility_runs")}
    for expected in (
        "status", "started_time", "finished_time", "trigger", "log_path",
        "documents_scanned", "already_decided_skipped", "newly_hidden",
        "newly_visible", "errors",
    ):
        assert expected in cols, expected


def test_current_row_defaults_to_the_sentinel_group(session):
    row = DocumentVisibilityCurrent(
        raw_document_id="raw-1", visible_search=False, visible_get=True,
    )
    session.add(row)
    session.flush()
    assert row.group_name == DOCUMENT_VISIBILITY_DEFAULT_GROUP


def test_run_defaults_to_running_status(session):
    run = DocumentVisibilityRun()
    session.add(run)
    session.flush()
    assert run.status == "running"
    assert run.started_time is not None
    assert run.finished_time is None
