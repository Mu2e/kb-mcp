"""Unit tests for strict question selection by audits (in-memory SQLite)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base
from kb_mcp.kb.eval.db_models import EvalAudit, EvalDataset, EvalGeneration, get_eval_questions


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[EvalGeneration.__table__, EvalDataset.__table__, EvalAudit.__table__])
    s = sessionmaker(bind=engine)()
    s.add(EvalGeneration(id="g", generation_type="synthetic", source_type="text"))
    yield s
    s.close()


def _question(s, qid, *audits):
    s.add(EvalDataset(id=qid, question=qid, generation_id="g"))
    t0 = datetime(2026, 9, 27, tzinfo=timezone.utc)
    for i, (audit_type, ok) in enumerate(audits):
        s.add(EvalAudit(question_id=qid, audit_type=audit_type, is_valid=ok, created_time=t0 + timedelta(minutes=i)))
    s.flush()


def _strict(s):
    return {q.id for q in get_eval_questions(generation_id="g", audit_filter={"strict": True}, session=s)}


def test_strict_selection(session):
    _question(session, "passed", ("llm_judge", True), ("overlap_check", True))
    _question(session, "overlap-rejected", ("llm_judge", True), ("overlap_check", False))
    _question(session, "llm-rejected", ("llm_judge", False))
    _question(session, "unaudited")
    _question(session, "human-rescued", ("llm_judge", False), ("human_review", True))
    _question(session, "human-rejected", ("llm_judge", True), ("human_review", False))
    _question(session, "human-changed-mind", ("human_review", False), ("human_review", True))
    assert _strict(session) == {"passed", "human-rescued", "human-changed-mind"}


def test_plain_filter_still_accepts_any_valid_audit(session):
    _question(session, "overlap-rejected", ("llm_judge", True), ("overlap_check", False))
    selected = {q.id for q in get_eval_questions(generation_id="g", audit_filter={"is_valid": True}, session=session)}
    assert selected == {"overlap-rejected"}


def test_grid_treats_partial_runs_as_unfinished(session):
    from kb_mcp.kb.eval.db_models import EvalResult, EvalRetrievedDocument, EvalRun
    from kb_mcp.kb.eval import grid

    Base.metadata.create_all(session.get_bind(), tables=[EvalRun.__table__, EvalResult.__table__, EvalRetrievedDocument.__table__])
    for qid in ("q1", "q2", "q3"):
        _question(session, qid)
    session.add(EvalRun(id="r", name="grid-x", generation_id="g", max_results=10))
    for qid in ("q1", "q2"):
        session.add(EvalResult(run_id="r", question_id=qid, is_hit=False))
    session.flush()

    assert grid._run_result_count(session, "grid-x") == 2
    assert grid._run_has_results(session, "grid-x")          # any result
    assert not grid._run_has_results(session, "grid-x", 3)   # but not all three
    assert grid._run_has_results(session, "grid-x", 2)
    assert grid._delete_runs(session, "grid-x") == 1
    assert grid._run_result_count(session, "grid-x") == 0
