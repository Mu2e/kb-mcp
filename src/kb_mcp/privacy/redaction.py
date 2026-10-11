"""Redact secrets and private details from what the MCP tools return.

The sensitivity scan (`sensitivity.py`) finds passages an agent should not
pass to an external LLM; for most of them -- a Zoom passcode in an ops
newsletter, a private mobile number in a report -- hiding the whole document
would lose far more than it protects. Redaction replaces just the passage
with a placeholder such as "[redacted: meeting passcode]" in the MCP output.

Two kinds of rules:

- GLOBAL_PATTERNS, defined here: formats that are sensitive wherever they
  occur (meeting passcodes and pwd= tokens, purchase-card identifiers, API
  keys, private keys). They apply to every document, including future
  imports, with no review needed.
- Per-document spans in `document_redactions` (`DocumentRedaction`): an
  exact string, usually approved from a scan finding, redacted in the
  output of that raw document (or of every document, for a global span).

Nothing in the database changes: stored text, chunks and summaries keep the
original, the CLI and web UI see it, and a rule is undone by deactivating
it. Only the agent-facing MCP entry points call this module, the same split
as document visibility.
"""

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PLACEHOLDER = "[redacted: {label}]"


@dataclass(frozen=True)
class Pattern:
    """A global redaction rule: `regex`'s `group` is replaced by the placeholder."""

    name: str
    label: str
    regex: "re.Pattern[str]"
    group: int = 0


# A value that only looks like a secret: placeholders, prompts, empties.
_NOT_A_SECRET = r"(?!\[redacted|<|\*{3}|x{3}|none\b|n/?a\b|your\b|the\b|is\b|required\b|same\b|see\b|tbd\b)"

GLOBAL_PATTERNS: Sequence[Pattern] = (
    Pattern(
        "meeting-link-pwd", "meeting passcode",
        # PDF text breaks long links with a space; take one continuation
        # that contains a digit (token tails do, ordinary words do not).
        re.compile(r"[?&]pwd=([A-Za-z0-9._\-]{6,}(?:\s(?=[A-Za-z._\-]*\d)[A-Za-z0-9._\-]{4,})?)"), 1,
    ),
    Pattern(
        "passcode-or-password", "credential",
        re.compile(
            # "pw" only with a colon: "pw = shaping constant" is physics. No
            # "pin" here: "Pin: GND" is a pinout (PIN codes are a separate pattern).
            r"(?:\b(?:pass\s?code|password|passwd|pwd)\b\s*[:=]|\bpw\s*:)\s*" + _NOT_A_SECRET
            + r"([^\s,;\"'<>()\[\]]{3,40})",
            re.IGNORECASE,
        ), 1,
    ),
    Pattern(
        # Only an upper-case PIN with a 4-8 digit value: pin numbers in a
        # pinout ("Pin: 3", "PIN 12: VCC") stay as they are.
        "pin-code", "credential",
        re.compile(r"\bPIN\s*[:=]\s*(\d{4,8})\b"), 1,
    ),
    Pattern(
        "purchase-card", "payment card",
        # The last four digits may sit in the next table cell ("Card#NAME | 1234").
        re.compile(r"\bCard#\s?[A-Z][A-Z0-9]{1,15}(?:[\s|]+\d{4})?\b"),
    ),
    Pattern(
        "private-key", "private key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL),
    ),
    Pattern(
        "api-token", "access token",
        re.compile(
            r"\b(?:AKIA[0-9A-Z]{16}"                     # AWS access key id
            r"|gh[pousr]_[A-Za-z0-9]{36,}"               # GitHub token
            r"|github_pat_[A-Za-z0-9_]{40,}"
            r"|xox[abprs]-[A-Za-z0-9-]{10,}"             # Slack token
            r"|sk-[A-Za-z0-9_-]{20,}"                    # OpenAI-style key
            r"|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"  # JWT
        ),
    ),
)


@dataclass(frozen=True)
class SpanRule:
    """A per-document (or global) exact-string redaction, from document_redactions."""

    id: str
    raw_document_id: Optional[str]
    span: str
    label: str
    regex: "re.Pattern[str]"


_PHONE_LIKE = re.compile(r"^\+?[\d\s\-.()/]+$")


def span_regex(span: str) -> "re.Pattern[str]":
    """Regex matching `span` with any whitespace between its words, ignoring case.

    Parsed text re-wraps lines and collapses spaces differently across
    documents, chunks and summaries, so a literal match would miss copies.
    A phone-like span (digits and separators only, at least 7 digits)
    matches its digits with any separators in between, so "630-803-5268"
    also catches "630 -803-5268", "(630) 803 5268" and "6308035268".
    """
    words = span.split()
    if not words:
        raise ValueError("empty redaction span")
    digits = re.sub(r"\D", "", span)
    if _PHONE_LIKE.match(span.strip()) and len(digits) >= 7:
        return re.compile(r"(?<!\d)\(?" + r"[\s\-.()/]*".join(digits) + r"(?!\d)")
    # Whole words only: a span "Mark" must not redact inside "benchmark".
    start = r"(?<!\w)" if words[0][0].isalnum() or words[0][0] == "_" else ""
    end = r"(?!\w)" if words[-1][-1].isalnum() or words[-1][-1] == "_" else ""
    return re.compile(start + r"\s+".join(re.escape(w) for w in words) + end, re.IGNORECASE)


Replacement = Tuple[int, int, str]  # (start, end, placeholder) in the input text


def find_replacements(text: str, rules: Iterable[SpanRule] = (), patterns: Sequence[Pattern] = GLOBAL_PATTERNS) -> List[Replacement]:
    """Non-overlapping regions of `text` to replace, sorted by position.

    Per-document spans come first, so where a span and a pattern overlap the
    span's (usually more specific) label wins; on overlap the earlier, then
    longer, region is kept.
    """
    found: List[Tuple[int, int, str, int]] = []
    for priority, (rx, group, label) in enumerate(
        [(r.regex, 0, r.label) for r in rules] + [(p.regex, p.group, p.label) for p in patterns]
    ):
        for m in rx.finditer(text):
            start, end = m.span(group)
            if end > start:
                found.append((start, end, PLACEHOLDER.format(label=label), priority))
    found.sort(key=lambda r: (r[0], -(r[1] - r[0]), r[3]))
    merged: List[Replacement] = []
    for start, end, placeholder, _ in found:
        if merged and start < merged[-1][1]:
            if end > merged[-1][1]:  # extends past the kept region: widen it
                merged[-1] = (merged[-1][0], end, merged[-1][2])
            continue
        merged.append((start, end, placeholder))
    return merged


def apply_replacements(text: str, replacements: Sequence[Replacement]) -> str:
    parts, pos = [], 0
    for start, end, placeholder in replacements:
        parts.append(text[pos:start])
        parts.append(placeholder)
        pos = end
    parts.append(text[pos:])
    return "".join(parts)


def offset_mapper(replacements: Sequence[Replacement], end: bool = False) -> Callable[[int], int]:
    """Map a position in the original text to the redacted text.

    A position inside a replaced region maps to the start of its
    placeholder, or with `end=True` (for the end of a range) to its end, so
    an excerpt computed on original offsets always holds a whole
    placeholder and never half of a secret.
    """
    def mapped(pos: int) -> int:
        shift = 0
        for start, stop, placeholder in replacements:
            if pos <= start:
                break
            if pos < stop:
                return start + shift + (len(placeholder) if end else 0)
            shift += len(placeholder) - (stop - start)
        return pos + shift
    return mapped


def redact(text: str, rules: Iterable[SpanRule] = (), patterns: Sequence[Pattern] = GLOBAL_PATTERNS) -> str:
    if not text:
        return text
    return apply_replacements(text, find_replacements(text, rules, patterns))


# --- rule cache ---------------------------------------------------------------

_CACHE_SECONDS = 60
_cache_lock = threading.Lock()
_cache: Dict[str, object] = {"loaded": 0.0, "by_raw": {}, "global": [], "ever_loaded": False}


class RedactionRulesUnavailable(RuntimeError):
    """The redaction rules have never been read in this process, so output cannot be redacted."""


def _load_rules() -> Tuple[Dict[str, List[SpanRule]], List[SpanRule]]:
    from ..kb.database import get_db_session
    from ..kb.db_models import DocumentRedaction

    by_raw: Dict[str, List[SpanRule]] = {}
    global_rules: List[SpanRule] = []
    with get_db_session() as session:
        rows = session.query(
            DocumentRedaction.id, DocumentRedaction.raw_document_id,
            DocumentRedaction.span, DocumentRedaction.label,
        ).filter(DocumentRedaction.active.is_(True)).all()
    for rid, raw_id, span, label in rows:
        try:
            rule = SpanRule(rid, raw_id, span, label or "private detail", span_regex(span))
        except ValueError:
            continue
        if raw_id:
            by_raw.setdefault(raw_id, []).append(rule)
        else:
            global_rules.append(rule)
    return by_raw, global_rules


def rules_for(raw_document_id: Optional[str]) -> List[SpanRule]:
    """Active span rules that apply to a raw document: its own plus the global ones.

    Rules are cached for a minute, so a new rule reaches a running server
    within that time without a query per tool call. If the table cannot be
    read, the last loaded rules stay in use and the global patterns, which
    need no database, still apply. Before the first successful load there are
    no last rules to fall back on, so this fails closed: it raises
    RedactionRulesUnavailable (and retries on the next call) rather than let
    output through without its per-document redactions.
    """
    now = time.time()
    with _cache_lock:
        if now - _cache["loaded"] > _CACHE_SECONDS:
            try:
                _cache["by_raw"], _cache["global"] = _load_rules()
                _cache["error"] = None
                _cache["ever_loaded"] = True
                _cache["loaded"] = now
            except Exception as e:  # noqa: BLE001 - patterns still apply; retried next minute
                message = str(e).splitlines()[0]
                if message != _cache.get("error"):  # once per distinct failure, not every minute
                    logger.error(f"redaction: could not load document_redactions, "
                                 f"using global patterns and the last loaded rules: {message}")
                _cache["error"] = message
                if not _cache["ever_loaded"]:
                    raise RedactionRulesUnavailable(
                        f"redaction rules could not be loaded: {message}") from e
                _cache["loaded"] = now
        by_raw, global_rules = _cache["by_raw"], _cache["global"]
    return list(by_raw.get(raw_document_id, [])) + list(global_rules) if raw_document_id else list(global_rules)


def clear_cache() -> None:
    with _cache_lock:
        _cache.update({"loaded": 0.0, "by_raw": {}, "global": [], "ever_loaded": False})


def redact_document_text(text: Optional[str], raw_document_id: Optional[str]) -> Optional[str]:
    """Redact text from one document with its span rules and the global patterns."""
    if not text:
        return text
    return redact(text, rules_for(raw_document_id))


def redact_output(text: Optional[str]) -> Optional[str]:
    """Catch-all for a tool's whole output: global patterns and global span rules only."""
    if not text:
        return text
    return redact(text, rules_for(None))


# Label text a finding's span often includes around the secret itself
# ("Passcode: 640919", "pwd=..."): not sensitive, so it may stay unredacted.
_LABEL_WORDS = re.compile(
    r"(?i)\b(?:zoom|meeting|phone|pass\s?code|password|passwd|pwd|pw|pin)\b|[\s:=#\-|?&]"
)


def covered_by_redaction(finding: dict, text: str, replacements: Sequence[Replacement]) -> bool:
    """Whether redaction already removes every occurrence of a finding's span from `text`.

    `replacements` are find_replacements() of `text` with the document's
    rules. A span is judged where it occurs in the document, so "mu2eops"
    counts as covered when every copy sits behind "Passcode:" and the
    pattern redacts it, even though the bare word matches no pattern; label
    words left around a redacted secret do not count against it.
    Restriction notices are never "covered": they need a decision about the
    document, not a redaction.
    """
    if finding.get("category") == "restricted":
        return False
    span = (finding.get("span") or "").strip()
    if not span:
        return False
    try:
        hits = [m.span() for m in span_regex(span).finditer(text or "")]
    except ValueError:
        return False
    if not hits:
        return False  # paraphrased span: cannot tell, keep it for review

    def covered(hs: int, he: int) -> bool:
        redacted = [rs < he and hs < re_ for rs, re_, _ in replacements]
        if not any(redacted):
            return False  # nothing of it is redacted, whatever it consists of
        kept = "".join(
            ch for i, ch in enumerate(text[hs:he], start=hs)
            if not any(rs <= i < re_ for rs, re_, _ in replacements)
        )
        return not _LABEL_WORDS.sub("", kept)

    return all(covered(hs, he) for hs, he in hits)
