"""Embedding-window budget for the chunkers.

Chunkers used to size chunks in tiktoken ``cl100k_base`` tokens against a
hardcoded cap that had no relationship to the embedding model. The models we
actually use are BERT-family encoders with a hard word-piece window
(MiniLM-L6-v2: 256, bge-small-en-v1.5: 512); anything past it is silently
truncated at embed time, so the tail of an oversized chunk is embedded as
nothing at all. Measured on this corpus, tiktoken undercounts word-pieces by
up to 1.32x, so even a "safe-looking" tiktoken budget overflowed.

Two further costs have to come out of that window before the chunk body gets
any of it, because :meth:`Chunk.embed_text` prepends them at embed time:

    Section: {section_path}

    Context: {document.gist}

    {chunk.text}

plus the encoder's own ``[CLS]``/``[SEP]``. This module measures all of that
in the *embedding model's own tokenizer* and hands the chunker what is left.
That deletes the two guess-factors the alternative needed (a worst-case
tiktoken->word-piece ratio and a worst-case prefix size) and self-adjusts when
the model or the gist changes.

When the real tokenizer is unreachable — unit tests, offline runs, an API
provider with no local tokenizer — it falls back to
``count_tokens(text) * 1.32`` (the measured worst-case ratio) against a
conservative window, so the arithmetic is identical in shape and errs toward
chunks that are too small rather than truncated.
"""

import logging
import math
import threading
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# [CLS] and [SEP] on BERT-family encoders. Reserved unconditionally: the
# tokenizer counts below all pass add_special_tokens=False.
SPECIAL_TOKENS = 2

# Measured tiktoken(cl100k_base) -> word-piece ratio over this corpus:
# min 0.90, median 1.06, max 1.32. Only used when the real tokenizer is
# unreachable; the max is the right choice there because overflow is silent.
FALLBACK_RATIO = 1.32

# Never hand the chunker a budget below this, however large the gist is —
# a document with a pathological gist should still produce usable chunks
# (the prefix is what gets crowded out at embed time, not the body).
MIN_CONTENT_BUDGET = 64

# Window fallbacks for when the model cannot be loaded to ask it directly.
# Conservative by design: a too-small window costs recall at the margin, a
# too-large one silently truncates.
_KNOWN_WINDOWS = {
    "all-MiniLM-L6-v2": 256,
    "all-MiniLM-L12-v2": 256,
    "multi-qa-MiniLM-L6-cos-v1": 512,
    "all-mpnet-base-v2": 384,
    "bge-small-en-v1.5": 512,
    "bge-base-en-v1.5": 512,
    "bge-large-en-v1.5": 512,
}
_DEFAULT_ST_WINDOW = 256
_DEFAULT_API_WINDOW = 8191

_resolved: Optional[Tuple[int, Callable[[str], int], bool]] = None
_resolve_lock = threading.Lock()


def _fallback_counter() -> Callable[[str], int]:
    from ...chunking import count_tokens

    def count(text: str) -> int:
        return math.ceil(count_tokens(text) * FALLBACK_RATIO)

    return count


def _tokenizer_counter(tokenizer) -> Callable[[str], int]:
    def count(text: str) -> int:
        if not text:
            return 0
        # verbose=False suppresses HF's "sequence longer than model maximum"
        # warning — measuring an over-long span is exactly this function's job.
        return len(tokenizer.encode(text, add_special_tokens=False, verbose=False))

    return count


def _resolve() -> Tuple[int, Callable[[str], int], bool]:
    """Resolve (window, counter, exact) for the configured embedder, once.

    Uses the shared :func:`get_embedder` cache, so this reuses the already
    loaded SentenceTransformer rather than paying for a second copy of the
    weights.
    """
    global _resolved
    if _resolved is not None:
        return _resolved

    with _resolve_lock:
        if _resolved is not None:
            return _resolved

        window: Optional[int] = None
        counter: Optional[Callable[[str], int]] = None
        exact = False

        try:
            from .utils import get_embedder

            embedder = get_embedder()
            window = int(embedder.max_tokens)
            tokenizer = getattr(getattr(embedder, "_model", None), "tokenizer", None)
            if tokenizer is not None:
                counter = _tokenizer_counter(tokenizer)
                exact = True
                logger.debug(
                    "Embed budget: %s window=%d, measured with the model's tokenizer",
                    getattr(embedder, "model", "?"),
                    window,
                )
        except Exception as exc:  # offline, no weights, API provider, tests
            logger.info("Embed budget: falling back to estimate (%s)", exc)

        if window is None:
            from ...config import get_embedding_config

            try:
                cfg = get_embedding_config()
                model = cfg.get("model") or ""
                provider = (cfg.get("provider") or "").lower()
            except Exception:
                model, provider = "", ""
            # Match on the bare name: EMBEDDING_MODEL is org-qualified for
            # some models ("BAAI/bge-small-en-v1.5"), and missing the table
            # here would silently halve the window on the fallback path.
            window = _KNOWN_WINDOWS.get(
                model.split("/")[-1],
                _DEFAULT_API_WINDOW if provider == "openai" else _DEFAULT_ST_WINDOW,
            )

        if counter is None:
            counter = _fallback_counter()
            logger.info(
                "Embed budget: window=%d, sizes estimated as tiktoken x %.2f",
                window,
                FALLBACK_RATIO,
            )

        _resolved = (window, counter, exact)
        return _resolved


def reset_cache() -> None:
    """Forget the resolved tokenizer/window. For tests and model switches."""
    global _resolved
    with _resolve_lock:
        _resolved = None


class EmbedBudget:
    """How many tokens of chunk body actually fit in the embedding window.

    Mirrors :meth:`Chunk.embed_text` exactly: the prefix parts it would
    prepend are measured in the same tokenizer that will encode them, and
    what remains of the window is the chunker's budget.
    """

    def __init__(
        self,
        gist: Optional[str] = None,
        prepend_section_path: bool = True,
        prepend_gist: bool = True,
    ):
        self.window, self._count, self.exact = _resolve()
        self.gist = gist
        self.prepend_section_path = prepend_section_path
        self.prepend_gist = prepend_gist
        self._gist_prefix = (
            f"Context: {gist}" if (prepend_gist and gist) else None
        )
        self._budget_cache: dict = {}

    def count(self, text: str) -> int:
        """Token count in the embedding model's units."""
        return self._count(text)

    def prefix_tokens(self, section_path: Optional[str] = None) -> int:
        """Tokens `embed_text` will prepend ahead of the chunk body."""
        parts = []
        if self.prepend_section_path and section_path:
            parts.append(f"Section: {section_path}")
        if self._gist_prefix:
            parts.append(self._gist_prefix)
        if not parts:
            return 0
        # Trailing separator included: embed_text joins body on with "\n\n".
        return self._count("\n\n".join(parts) + "\n\n")

    def content_budget(self, section_path: Optional[str] = None) -> int:
        """Tokens of chunk body that fit once prefix and specials are paid."""
        key = section_path or ""
        cached = self._budget_cache.get(key)
        if cached is not None:
            return cached
        budget = self.window - SPECIAL_TOKENS - self.prefix_tokens(section_path)
        budget = max(MIN_CONTENT_BUDGET, budget)
        self._budget_cache[key] = budget
        return budget


# Measured over 500 corpus gists, as `Context: {gist}\n\n` in word-pieces:
# p50 43, p90 57, p99 69, max 72. The token chunker's size has to be one
# number for the whole corpus (it names the strategy), so it reserves the
# worst case rather than a typical one — a document with a long gist would
# otherwise overflow silently, which is the failure this all exists to stop.
GIST_ALLOWANCE = 72


def token_chunk_size(window: Optional[int] = None) -> int:
    """Default `chunk_size` for the token chunker, in **tiktoken** units.

    The token chunker slices by tiktoken offsets, but the encoder reads
    word-pieces and truncates at its window without complaining. `chunk_size`
    was left at 1000 — an 8191-window OpenAI-era default — long after the
    model became a 256-window MiniLM, so ~89% of token chunks had roughly
    three quarters of their text embedded as nothing.

    So: take the window, pay the encoder's specials and the worst-case gist
    prefix `embed_text()` prepends, then convert word-pieces to tiktoken with
    the measured worst-case ratio.

    Depends only on the window, never on the document — the number lands in
    the strategy name (`tokens_137_14`), which has to mean the same thing for
    every row.
    """
    if window is None:
        window, _counter, _exact = _resolve()
    content = window - SPECIAL_TOKENS - GIST_ALLOWANCE
    return max(MIN_CONTENT_BUDGET, int(content / FALLBACK_RATIO))


def get_embed_budget(
    gist: Optional[str] = None,
    prepend_section_path: bool = True,
    prepend_gist: bool = True,
) -> EmbedBudget:
    """Budget for one document. Cheap — the tokenizer resolution is cached."""
    return EmbedBudget(
        gist=gist,
        prepend_section_path=prepend_section_path,
        prepend_gist=prepend_gist,
    )
