"""Unit tests for eval metrics arithmetic (hit rate, recall@k, summary stats).

Runs on a private in-memory SQLite engine with synthetic EvalRun/EvalResult
rows — verifies the merged metrics/CLI union references only fields that
exist and computes the expected numbers.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base
from kb_mcp.kb.eval.db_models import EvalRun, EvalResult
from kb_mcp.kb.eval.metrics import (
    compute_hit_rate,
    compute_recall_at_k,
    get_rank_distribution,
    get_summary_stats,
)


@pytest.fixture()
def eval_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def run_with_results(eval_session):
    """5 questions: hits at ranks 1, 2, 5; two misses."""
    run = EvalRun(id="run-metrics-test", name="metrics", max_results=10)
    eval_session.add(run)
    ranks = [1, 2, 5, None, None]
    for i, rank in enumerate(ranks):
        eval_session.add(EvalResult(
            id=f"res-{i}",
            run_id=run.id,
            question_id=f"q-{i}",
            is_hit=rank is not None,
            hit_rank=rank,
        ))
    eval_session.commit()
    return run


def test_hit_rate(eval_session, run_with_results):
    rate = compute_hit_rate(run_id=run_with_results.id, session=eval_session)
    assert rate == pytest.approx(3 / 5)


def test_recall_at_k_arithmetic(eval_session, run_with_results):
    recall = compute_recall_at_k(
        run_id=run_with_results.id, k_values=[1, 3, 5, 10], session=eval_session
    )
    assert recall[1] == pytest.approx(1 / 5)
    assert recall[3] == pytest.approx(2 / 5)
    assert recall[5] == pytest.approx(3 / 5)
    # No hits beyond rank 5 — recall plateaus.
    assert recall[10] == pytest.approx(3 / 5)


def test_recall_at_k_skips_nonpositive_k(eval_session, run_with_results):
    recall = compute_recall_at_k(
        run_id=run_with_results.id, k_values=[0, -3, 5], session=eval_session
    )
    assert list(recall.keys()) == [5]


def test_rank_distribution(eval_session, run_with_results):
    dist = get_rank_distribution(run_id=run_with_results.id, session=eval_session)
    assert dist == {1: 1, 2: 1, 5: 1}


def test_summary_stats_union_keys(eval_session, run_with_results):
    """The merged CLI display reads these exact keys — guard them."""
    stats = get_summary_stats(run_id=run_with_results.id, session=eval_session)
    assert stats["total_questions"] == 5
    assert stats["hits"] == 3
    assert stats["misses"] == 2
    assert stats["hit_rate"] == pytest.approx(3 / 5)
    assert stats["rank_distribution"] == {1: 1, 2: 1, 5: 1}
    assert stats["recall_at_k"][5] == pytest.approx(3 / 5)
    # No judge rows -> no judge keys.
    assert "judge_total_questions" not in stats


def test_summary_stats_with_judge(eval_session):
    run = EvalRun(id="run-judge-test", name="judge", max_results=10)
    eval_session.add(run)
    for i, (rank, judge) in enumerate([(1, True), (None, False), (2, True)]):
        eval_session.add(EvalResult(
            id=f"jres-{i}",
            run_id=run.id,
            question_id=f"jq-{i}",
            is_hit=rank is not None,
            hit_rank=rank,
            is_judge_hit=judge,
        ))
    eval_session.commit()

    stats = get_summary_stats(run_id=run.id, session=eval_session)
    assert stats["judge_total_questions"] == 3
    assert stats["judge_hits"] == 2
    assert stats["judge_hit_rate"] == pytest.approx(2 / 3)
    assert "judge_recall_at_k" in stats
