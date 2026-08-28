"""Unit tests for LLM token accounting.

The counters here are the only record of what building the knowledge base
costs, so the cases that matter are the ways OpenAI-compatible endpoints
under-report: a missing `total_tokens`, missing cache details, or no `usage`
object at all.
"""

import kb_mcp.llm.usage as usage_mod
from kb_mcp.llm.usage import UsageAccumulator, usage_snapshot


class _Usage:
    """Stand-in for a provider usage object (attribute access, not a dict)."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_synthesizes_missing_total():
    # Providers that omit total_tokens shouldn't report a zero total.
    snapshot = usage_snapshot(_Usage(prompt_tokens=100, completion_tokens=20))
    assert snapshot["total_tokens"] == 120


def test_reads_cached_tokens_from_prompt_details():
    snapshot = usage_snapshot(
        _Usage(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=_Usage(cached_tokens=30),
        )
    )
    assert snapshot["cached_prompt_tokens"] == 30
    # Active context excludes what came from the cache.
    assert snapshot["main_context_tokens"] == 70


def test_missing_usage_yields_zeros_not_an_error():
    assert usage_snapshot(None)["total_tokens"] == 0


def test_snapshot_is_idempotent():
    # Ingest re-normalizes aggregated snapshots when flushing parser-stage
    # counters; that must not lose the cache figures.
    first = usage_snapshot(
        _Usage(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=_Usage(cached_tokens=30),
        )
    )
    assert usage_snapshot(first) == first


def test_accumulator_totals_and_stage_breakdown():
    acc = UsageAccumulator()
    acc.add(_Usage(prompt_tokens=10, completion_tokens=5), "image_description", "vl")
    acc.add(_Usage(prompt_tokens=20, completion_tokens=5), "image_description", "vl")
    acc.add(_Usage(prompt_tokens=7, completion_tokens=1), "table_summary", "chat")

    summary = acc.summary()
    assert summary["totals"]["prompt_tokens"] == 37
    assert summary["totals"]["completion_tokens"] == 11
    assert summary["totals"]["requests"] == 3

    images = summary["by_stage"]["image_description"]
    assert images["prompt_tokens"] == 30
    assert images["requests"] == 2
    # Model is retained so aggregated rows stay attributable.
    assert images["model"] == "vl"


def test_empty_usage_warns_once_per_stage_and_model(caplog):
    # A document with hundreds of figures must not emit one warning per call.
    usage_mod._warned_empty.clear()
    acc = UsageAccumulator()
    with caplog.at_level("WARNING"):
        for _ in range(5):
            acc.add(None, stage="image_description", model="silent")

    warnings = [r for r in caplog.records if "No token usage reported" in r.message]
    assert len(warnings) == 1
    # The calls are still counted, so "0 tokens over 5 requests" is visible.
    assert acc.summary()["totals"]["requests"] == 5


def test_reported_usage_does_not_warn(caplog):
    usage_mod._warned_empty.clear()
    acc = UsageAccumulator()
    with caplog.at_level("WARNING"):
        acc.add(_Usage(prompt_tokens=5, completion_tokens=1), "table_summary", "chat")
    assert not [r for r in caplog.records if "No token usage reported" in r.message]
