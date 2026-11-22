"""Semantic searches against the real knowledge base stay fast, per filter kind.

Opt-in: these hit the configured PostgreSQL knowledge base (the production
one, when that is what the env file points at), embed real queries, and take
a few minutes, so they run only with KB_PERF_TESTS=1:

    KB_PERF_TESTS=1 pytest tests/integration/test_search_latency_pg.py -v -s

What they guard (measured 2026-11-12 from `logs_search`): a filtered semantic
search used to take 25 to 37 minutes, or hit the 30 s statement timeout,
because counting the chunks a filter passes was slow for broad filters
(`doc_type=image` timed out, `source_id=mu2e-docdb` took 14 s cold). The
summary view `kb_chunk_counts` lets such a filter skip the count; this file
fails if a filter kind that real users send is slow again, or errors.

Each filter kind runs a few queries twice. The first pass must not raise (a
timeout surfaces as an exception) -- it is not timed, because it includes
loading the embedding model and a cold database cache, so it runs with a long
statement timeout (the first table-filtered search after the cache has gone
cold needs ~40 s; the production bound is 30 s, so that one search can still
time out for a user). The second pass is timed, under the production bound:
its slowest query must stay under KB_PERF_MAX_S (default 5 s; typical is 0.3
to 1.5 s). Set KB_PERF_MAX_S higher on a slow or busy database.

Skipped unless KB_PERF_TESTS=1 and the knowledge base is reachable.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KB_PERF_TESTS") != "1",
    reason="set KB_PERF_TESTS=1 to run the search latency tests against the real database",
)

MAX_S = float(os.environ.get("KB_PERF_MAX_S", "5"))

QUERIES = (
    "tracker straw tube alignment",
    "calorimeter crystal energy resolution",
    "cosmic ray veto efficiency",
)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def corpus():
    """A doc_id prefix that exists, or skip when the knowledge base is not there."""
    from sqlalchemy import text

    from kb_mcp.kb import get_db_session

    try:
        with get_db_session() as session:
            doc_id = session.execute(
                text("SELECT doc_id FROM documents WHERE source_id = 'mu2e-docdb' "
                     "AND doc_type = 'text' AND doc_id IS NOT NULL LIMIT 1")
            ).scalar()
    except Exception as e:  # noqa: BLE001 - any connection problem means skip
        pytest.skip(f"knowledge base not reachable: {e}")
    if not doc_id:
        pytest.skip("no mu2e-docdb documents")
    with get_db_session() as session:
        day = session.execute(text(
            "SELECT creating_time::date FROM documents WHERE creating_time IS NOT NULL "
            "ORDER BY creating_time DESC OFFSET 5000 LIMIT 1")).scalar()
    day = day.isoformat() if day else _iso(365)
    return {"doc_id_prefix": doc_id[:3], "doc_id": doc_id, "day": day}


def _cases(corpus):
    return {
        "no filter": {},
        "source_id": {"filter": {"term": {"source_id": "mu2e-docdb"}}},
        "doc_type=text": {"filter": {"term": {"doc_type": "text"}}},
        "doc_type=image": {"filter": {"term": {"doc_type": "image"}}},
        "doc_type=table": {"filter": {"term": {"doc_type": "table"}}},
        "source_id and doc_type": {"filter": {"bool": {"must": [
            {"term": {"source_id": "mu2e-docdb"}}, {"term": {"doc_type": "text"}}]}}},
        "time range, update_time since 90 days": {"filter": {"range": {"update_time": {"gte": _iso(90)}}}},
        "time range, creating_time 2 years": {"filter": {"range": {"creating_time": {"gte": _iso(730)}}}},
        "exact docdb id": {"filter": {"term": {"doc_id": corpus["doc_id"]}}},
        "docdb id range": {"filter": {"range": {"doc_id": {"gte": "50000", "lte": "59999"}}}},
        "docdb id range and type": {"filter": {"bool": {"must": [
            {"range": {"doc_id": {"gte": "50000", "lte": "59999"}}}, {"term": {"doc_type": "text"}}]}}},
        "exact date": {"filter": {"range": {"creating_time": {"gte": corpus["day"], "lte": corpus["day"] + "T23:59:59"}}}},
        "date range, 1 month": {"filter": {"range": {"creating_time": {"gte": _iso(60), "lte": _iso(30)}}}},
        "date range and type": {"filter": {"bool": {"must": [
            {"range": {"creating_time": {"gte": _iso(365)}}}, {"term": {"doc_type": "text"}}]}}},
        "doc_id prefix": {"filter": {"wildcard": {"doc_id": corpus["doc_id_prefix"] + "*"}}},
    }


# The kinds of filter that must never need a live count again: the
# kb_chunk_counts view decides the path, so a missing view (run `kb tools
# db-maintain`) fails here. The expected path follows the sizes in the
# knowledge base against SEARCH_EXACT_MAX_CHUNKS (500000): text (1.0M on
# 2026-11-21) is above it and takes the index; images (427k) and tables
# (169k) are below it and are ranked exactly -- through the index every
# image-filtered query timed out (2026-11-21).
COARSE = {
    "source_id": "index", "doc_type=text": "index", "doc_type=image": "exact",
    "source_id and doc_type": "index", "doc_type=table": "exact",
}


@pytest.mark.parametrize("case", [
    "no filter", "source_id", "doc_type=text", "doc_type=image", "doc_type=table", "source_id and doc_type",
    "time range, update_time since 90 days", "time range, creating_time 2 years", "doc_id prefix",
    "exact docdb id", "docdb id range", "docdb id range and type", "exact date",
    "date range, 1 month", "date range and type",
])
def test_filtered_search_is_fast(case, corpus, monkeypatch):
    from kb_mcp.kb.search import search_semantic

    kwargs = _cases(corpus)[case]

    with monkeypatch.context() as m:          # warm-up pass: must not raise
        m.setenv("SEARCH_VECTOR_TIMEOUT_MS", "120000")
        for q in QUERIES:
            search_semantic(q, max_results=10, **kwargs)

    timings, paths, counts = [], set(), []
    for q in QUERIES:                         # timed pass
        t0 = time.time()
        out = search_semantic(q, max_results=10, **kwargs)
        timings.append(time.time() - t0)
        paths.add(out["metadata"].get("vector_path"))
        counts.append(len(out["results"]))

    print(f"\n{case}: " + ", ".join(f"{t:.2f}s" for t in timings)
          + f"  paths={sorted(map(str, paths))} results={counts}")
    assert max(timings) <= MAX_S, f"{case}: slowest {max(timings):.1f}s > {MAX_S}s ({timings})"
    assert any(counts), f"{case}: no query returned a result"
    if case in COARSE:
        assert paths == {COARSE[case]}, (
            f"{case}: expected the {COARSE[case]} path from the kb_chunk_counts view, got {paths}; "
            "run `kb tools db-maintain` if the view is missing")
