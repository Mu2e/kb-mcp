"""Backoff for LLM calls made against a throttling or saturated endpoint.

The openai SDK already retries twice on its own, but with a short schedule
tuned for a healthy hosted API. A self-hosted endpoint under a full-corpus
reparse needs to back off considerably further, and — more importantly — the
caller needs to be able to tell "the endpoint is refusing load" apart from
"this input genuinely failed", because the two call for opposite responses:
the first means slow down and retry, the second means record the failure and
move on.
"""

import logging
import random
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """A call exhausted its retries against a throttling or transient error.

    Kept distinct from a plain failure so callers can tell "the endpoint is
    refusing load" (retryable, affects every request equally, and means the
    whole run is degraded) from a genuine per-input failure.
    """


def is_throttling(exc: Exception) -> bool:
    """Whether `exc` is the endpoint asking us to slow down or retry later.

    Matches on the openai SDK's typed exceptions rather than status codes so it
    keeps working through the SDK's own transport-level wrapping. 5xx and
    timeouts are included: at a saturated endpoint they are indistinguishable
    from explicit throttling, and backing off is the right response to both.
    """
    try:
        import openai
    except ImportError:  # pragma: no cover - openai is a hard dependency here
        return False

    return isinstance(exc, (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.InternalServerError,
    ))


def call_with_backoff(fn, *args, max_attempts: int = 5, base_delay: float = 2.0, **kwargs):
    """Run `fn`, retrying throttling/transient errors with exponential backoff.

    Delays are 2s, 4s, 8s, 16s, each jittered to 50–150% of nominal so a thread
    pool's workers don't synchronise into a thundering herd when the endpoint
    recovers.

    Non-throttling errors (a text-only model, a malformed input) are re-raised
    immediately — they would fail identically on every attempt.

    Raises:
        RateLimited: every attempt hit a throttling/transient error.
    """
    last: Optional[Exception] = None
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if not is_throttling(e):
                raise
            last = e
            if attempt == max_attempts - 1:
                break
            delay = base_delay * (2 ** attempt)
            delay *= 0.5 + random.random()  # jitter: 50–150% of nominal
            logger.debug(
                "Throttled (%s), retrying in %.1fs (attempt %d/%d)",
                type(e).__name__, delay, attempt + 1, max_attempts,
            )
            time.sleep(delay)

    raise RateLimited(
        f"{type(last).__name__} after {max_attempts} attempts: {last}"
    ) from last
