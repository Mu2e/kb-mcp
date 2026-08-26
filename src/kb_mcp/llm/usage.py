"""Token accounting for LLM calls.

Every provider response carries a `usage` object; the counters in it are the
only record of what a knowledge-base build actually cost. The helpers here
normalize that object (providers disagree about which fields they populate),
accumulate it per stage, and hand it to `record_llm_usage` for persistence.

Two layers, so callers can pick what they need:

  - `usage_snapshot()` / `UsageAccumulator` — pure in-memory counting, no DB.
  - `record_llm_usage()` — writes one `llm_usage` row (see db_models).

`BaseAgent` predates this module and keeps its own copy of the accumulate
logic for its streaming/event needs; `usage_snapshot` here is the same
normalization, extracted so the ingest path doesn't have to import an agent.
"""

import logging
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Zero-usage warnings are logged once per (stage, model) rather than per call:
# a document with 400 figures would otherwise emit 400 identical lines.
_warned_empty: set = set()
_warned_lock = threading.Lock()

# Canonical stage names. Kept as constants so a report can group by stage
# without depending on every call site spelling the string the same way.
STAGE_TABLE_SUMMARY = "table_summary"
STAGE_IMAGE_DESCRIPTION = "image_description"
STAGE_DOCUMENT_SUMMARY = "document_summary"
STAGE_GRAPH_EXTRACTION = "graph_extraction"
STAGE_GRAPH_MATCHING = "graph_matching"
STAGE_PRIVACY_FILTER = "privacy_filter"
STAGE_EMBEDDING = "embedding"

USAGE_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "main_context_tokens",
    "cached_prompt_tokens",
)


def _int_field(obj: Any, name: str) -> int:
    """Read an integer counter from a usage object or plain dict."""
    if obj is None:
        return 0
    if isinstance(obj, dict):
        value = obj.get(name)
    else:
        value = getattr(obj, name, None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_field(obj: Any, name: str) -> bool:
    """True when `obj` carries `name` at all (as a dict key or attribute)."""
    if obj is None:
        return False
    if isinstance(obj, dict):
        return name in obj
    return hasattr(obj, name)


def usage_snapshot(usage: Any) -> Dict[str, int]:
    """Normalize a provider usage object into integer token counters.

    Tolerates the three ways OpenAI-compatible servers under-report:
    a missing `total_tokens` (synthesized from the parts), a missing
    `prompt_tokens_details` (no cache info), and a `usage` of None entirely.
    """
    prompt_tokens = _int_field(usage, "prompt_tokens")
    completion_tokens = _int_field(usage, "completion_tokens")
    total_tokens = _int_field(usage, "total_tokens")

    # Cached-token counts live under `prompt_tokens_details` in provider
    # responses, but at the top level in a snapshot this function already
    # produced. Accept both, so re-normalizing an aggregated snapshot (as
    # ingest does when flushing parser-stage counters) is lossless rather
    # than silently zeroing the cache figures.
    if isinstance(usage, dict):
        prompt_details = usage.get("prompt_tokens_details")
    else:
        prompt_details = getattr(usage, "prompt_tokens_details", None)

    if prompt_details is None and _has_field(usage, "cached_prompt_tokens"):
        cached_prompt_tokens = _int_field(usage, "cached_prompt_tokens")
    else:
        cached_prompt_tokens = _int_field(prompt_details, "cached_tokens")

    # Input context actually processed, i.e. excluding what the provider
    # served from its prompt cache.
    main_context_tokens = max(prompt_tokens - cached_prompt_tokens, 0)

    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "main_context_tokens": main_context_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
    }


def warn_if_unreported(snapshot: Dict[str, int], stage: str, model: Optional[str]) -> bool:
    """Log once per (stage, model) when a backend reports no usage at all.

    A zero in a cost report is ambiguous: it can mean "this stage never ran"
    or "this endpoint doesn't populate usage". Without this warning the
    second case silently reads as free.

    Returns:
        True if the snapshot was empty (nothing reported).
    """
    if snapshot["total_tokens"] > 0:
        return False

    key = (stage, model or "")
    with _warned_lock:
        if key in _warned_empty:
            return True
        _warned_empty.add(key)
    logger.warning(
        f"No token usage reported by model '{model or 'default'}' for stage "
        f"'{stage}' — token totals for this stage will read as zero even "
        f"though calls are being made. The endpoint likely omits the "
        f"'usage' field."
    )
    return True


class UsageAccumulator:
    """Thread-safe running total of token usage, broken down by stage.

    Ingest fans out across ThreadPoolExecutors (one worker per image, per
    table, per document), so `add()` is called concurrently and takes a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.totals: Dict[str, int] = {field: 0 for field in USAGE_FIELDS}
        self.totals["requests"] = 0
        self.by_stage: Dict[str, Dict[str, int]] = {}

    def add(self, usage: Any, stage: str = "llm_call", model: Optional[str] = None) -> Dict[str, int]:
        """Fold one response's usage into the totals; returns its snapshot."""
        snapshot = usage_snapshot(usage)
        warn_if_unreported(snapshot, stage, model)

        with self._lock:
            for field in USAGE_FIELDS:
                self.totals[field] += snapshot[field]
            self.totals["requests"] += 1

            bucket = self.by_stage.setdefault(
                stage, {**{f: 0 for f in USAGE_FIELDS}, "requests": 0, "model": model}
            )
            for field in USAGE_FIELDS:
                bucket[field] += snapshot[field]
            bucket["requests"] += 1
            # A stage is normally driven by one model; keep the first seen so
            # aggregated rows can still be attributed.
            if bucket.get("model") is None:
                bucket["model"] = model

        return snapshot

    def summary(self) -> Dict[str, Any]:
        """Return cumulative totals plus the per-stage breakdown."""
        with self._lock:
            return {
                "totals": dict(self.totals),
                "by_stage": {k: dict(v) for k, v in self.by_stage.items()},
            }

    def format_summary(self) -> str:
        """One-line human-readable total, for end-of-run logging."""
        with self._lock:
            t = self.totals
            parts = [
                f"{t['requests']} requests",
                f"in={t['prompt_tokens']}",
                f"out={t['completion_tokens']}",
                f"total={t['total_tokens']}",
            ]
            if t["cached_prompt_tokens"]:
                parts.append(f"cached={t['cached_prompt_tokens']}")
        return ", ".join(parts)


def record_llm_usage(
    usage: Any,
    stage: str,
    model: Optional[str] = None,
    document_id: Optional[str] = None,
    raw_document_id: Optional[str] = None,
    accumulator: Optional[UsageAccumulator] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Persist one call's token usage and optionally fold it into a total.

    Best-effort by design: token accounting must never be the reason a
    document fails to import, so a DB error here is logged and swallowed.
    Passing `accumulator` keeps an in-memory tally even when the write fails.

    Args:
        usage: The provider `usage` object off the response (may be None).
        stage: Which pipeline step this call belongs to, e.g.
            "image_description", "graph_extraction". Use the STAGE_*
            constants where one exists.
        model: Model name the call was routed to.
        document_id: FK → documents.id, when known.
        raw_document_id: FK → documents_raw.id, when known.
        accumulator: Optional in-memory tally to update.
        meta: Extra context to store alongside the counters.

    Returns:
        The usage snapshot (all zeros if nothing was reported).
    """
    if accumulator is not None:
        snapshot = accumulator.add(usage, stage=stage, model=model)
    else:
        snapshot = usage_snapshot(usage)
        warn_if_unreported(snapshot, stage, model)

    try:
        from ..kb.database import get_db_session
        from ..kb.db_models import LLMUsage

        # auto_expunge=False: the row is write-once and never read back here.
        with get_db_session(auto_expunge=False) as session:
            session.add(
                LLMUsage(
                    stage=stage,
                    model=model,
                    document_id=document_id,
                    raw_document_id=raw_document_id,
                    meta=meta or {},
                    **snapshot,
                )
            )
    except Exception as e:
        # Never let accounting break ingest.
        logger.debug(f"Could not record LLM usage for stage '{stage}': {e}")

    return snapshot
