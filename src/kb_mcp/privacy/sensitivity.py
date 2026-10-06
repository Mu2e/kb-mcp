"""LLM scan for content an agent should not surface to a non-local LLM.

Everything indexed here is readable by collaboration members anyway; the
risk this guards against is narrower: an MCP client (and the external LLM
behind it) pulling a personnel matter, a password or someone's medical
details into a conversation, unprompted or out of context. So the prompt
asks for a short list of specific harms, tells the model that almost every
document is fine, and names the routine collaboration content it must not
flag.

The scan reads a document's whole text in overlapping windows (image
descriptions and tables are already inline in the parent text). Each
finding quotes its evidence, and `evidence_found` records whether that
quote really occurs in the window, so a hallucinated finding is visible at
review time. Nothing here hides anything: findings are written for a human
to review (`kb tools visibility findings`), who then decides with `hide`.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..llm import STAGE_SENSITIVITY_SCAN, call_with_backoff, record_llm_usage

logger = logging.getLogger(__name__)

# Bump whenever the prompt or the output contract changes: scans are keyed on
# (document, prompt version), so a new version re-scans everything once.
PROMPT_VERSION = "v2"

CATEGORIES = {
    "personnel": "HR and personnel matters about identifiable people",
    "individual_assessment": "evaluations, criticism or rankings of identifiable people",
    "personal_data": "private contact, identity, health or family information",
    "credentials": "passwords, keys, tokens and other secrets",
    "security": "details that would help someone attack Mu2e or Fermilab systems",
    "restricted": "material explicitly marked confidential, embargoed, export controlled or under NDA",
    "other": "sensitive in a way none of the above covers",
}
SEVERITIES = ("low", "medium", "high")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Endpoints the scan may send document text to. The point of the scan is to
# keep this content away from external LLMs, so the scan itself must not be
# the leak: anything else needs an explicit --allow-endpoint.
DEFAULT_ALLOWED_HOSTS = ("vllm.fnal.gov", "inference-api.alcf.anl.gov")

_SYSTEM_PROMPT = """\
You review documents from the internal knowledge base of Mu2e, a particle physics \
experiment at Fermilab. Collaboration members can read all of these documents. The \
knowledge base is also searched by AI assistants that run on external, commercial \
LLMs. Your job is to find the rare passages that such an assistant should never \
surface or quote to anyone: content that would hurt a person, or the experiment, \
if it were pasted into a chat with an outside AI service.

Almost every document is fine. Report a finding only when you can quote the \
passage that causes it. Respond with JSON only."""

_USER_PROMPT_TEMPLATE = """\
Report passages in the text below that fall into one of these categories.

personnel: HR and personnel matters about an identifiable person: hiring or \
firing decisions, performance problems, disciplinary action, complaints or \
conflicts between people, the salary or pay of a named person, visa or \
immigration status, leave and the personal reasons for it.

individual_assessment: evaluations of identifiable people: reference letters, \
candidate rankings, review committee comments about a person's competence or \
conduct, award or promotion deliberations.

personal_data: private information about an identifiable person, i.e. \
information that belongs to their private life, not their job: home address, \
private mobile number or private email (e.g. gmail), date of birth, ID or \
passport numbers, health or medical information, family circumstances.

credentials: secrets that grant access: passwords (even if they look old or \
like examples), API keys, tokens, private keys, database connection strings \
with passwords, door or safe combinations.

security: information that would help someone attack Mu2e or Fermilab \
computing or control systems: unpatched vulnerabilities, how to bypass access \
controls, internal network layouts presented as security information.

restricted: material explicitly marked as confidential, embargoed, "do not \
distribute", "collaboration internal only", export controlled (ITAR/EAR) or \
under NDA, and vendor quotes or bids marked proprietary. Use this only when \
the document itself carries such a marking or clearly states the restriction.

other: content that is clearly sensitive in a way the categories above miss. \
Use rarely.

Do NOT report ordinary collaboration content, even when it names people:
- author lists, attendee lists, names with institution, role, work email or \
work phone, collaboration directories and shift schedules;
- business contact details of people or companies: work email addresses \
(e.g. @fnal.gov, @anl.gov, university addresses), office or company phone \
numbers, company addresses, email headers (From/To/Cc/Sent), vendor, \
supplier and sales contacts, signatures;
- meeting minutes, action items, technical discussion and disagreements about \
physics or engineering;
- project-level budgets, cost estimates, WBS numbers, labor in FTEs or by \
role, procurement of equipment, schedules and milestones;
- physics results, plots and drafts that carry no explicit restriction;
- hostnames, software versions or configuration that do not reveal a secret \
or a weakness.

Severity:
- high: must never reach an outside service. Any real credential; health, \
medical or family information; HR action or disciplinary matter about a named \
person; ID numbers.
- medium: should not come up in search results: assessments of named people, \
personal contact data, material explicitly marked confidential or embargoed, \
security details.
- low: probably fine, but worth a human look.

Document: {title}
Source: {source}
Type: {source_type}
Part {window} of {windows} of the document's text.

<text>
{text}
</text>

Return a JSON object:
{{"findings": [{{"category": "<one of: {categories}>", "severity": "<low|medium|high>", \
"subject": "<who or what is affected, e.g. 'a named postdoc', 'DAQ server account'>", \
"evidence": "<exact quote from the text, at most 300 characters>", \
"reason": "<one sentence>"}}]}}
Return {{"findings": []}} if nothing qualifies, which is the usual case."""


def build_messages(
    text: str,
    *,
    title: str = "",
    source: str = "",
    source_type: str = "",
    window: int = 1,
    windows: int = 1,
) -> List[Dict[str, str]]:
    """Chat messages for one window of a document."""
    user = _USER_PROMPT_TEMPLATE.format(
        title=title or "(untitled)",
        source=source or "(unknown)",
        source_type=source_type or "(unknown)",
        window=window,
        windows=windows,
        text=text,
        categories=", ".join(CATEGORIES),
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def split_windows(text: str, window_chars: int = 24000, overlap_chars: int = 800) -> List[str]:
    """Split text into overlapping windows, preferring paragraph or line breaks.

    Consecutive windows share `overlap_chars`, so a passage cut at a window
    edge still appears whole in one of them. A text no longer than
    `window_chars` is a single window.
    """
    if window_chars <= overlap_chars:
        raise ValueError("window_chars must be larger than overlap_chars")
    if len(text) <= window_chars:
        return [text]

    windows = []
    start = 0
    while start < len(text):
        end = start + window_chars
        if end >= len(text):
            windows.append(text[start:])
            break
        # Prefer to cut at a paragraph, then a line break, in the last fifth.
        floor = start + (window_chars * 4) // 5
        # Cut just after the break, so it ends this window.
        cut = text.rfind("\n\n", floor, end - 1)
        if cut != -1:
            cut += 2
        else:
            cut = text.rfind("\n", floor, end)
            cut = end if cut == -1 else cut + 1
        windows.append(text[start:cut])
        start = cut - overlap_chars
    return windows


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def evidence_in_text(evidence: str, text: str) -> bool:
    """Whether the quoted evidence occurs in the text, ignoring whitespace and case.

    Models trim or re-wrap quotes, so a long quote counts if any 60-character
    stretch of it matches.
    """
    ev = _normalize(evidence or "")
    if not ev:
        return False
    hay = _normalize(text)
    if ev in hay:
        return True
    if len(ev) > 60:
        return any(ev[i:i + 60] in hay for i in range(0, len(ev) - 59, 20))
    return False


def parse_findings(content: str) -> List[Dict[str, Any]]:
    """Parse and normalise the model's JSON answer.

    Raises ValueError when there is no JSON object to parse; an unknown
    category becomes "other" and an unknown severity "medium", so a sloppy
    answer still reaches review instead of being dropped.
    """
    content = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    elif not content.startswith("{"):
        brace = re.search(r"\{.*\}", content, re.DOTALL)
        if not brace:
            raise ValueError(f"no JSON object in response: {content[:200]!r}")
        content = brace.group(0)
    data = json.loads(content)

    raw = data.get("findings") if isinstance(data, dict) else None
    if raw is None:
        raise ValueError(f"response has no 'findings' list: {content[:200]!r}")
    findings = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        category = str(f.get("category", "")).strip().lower().replace(" ", "_")
        severity = str(f.get("severity", "")).strip().lower()
        findings.append({
            "category": category if category in CATEGORIES else "other",
            "severity": severity if severity in SEVERITY_RANK else "medium",
            "subject": str(f.get("subject", "")).strip()[:300],
            "evidence": str(f.get("evidence", "")).strip()[:1000],
            "reason": str(f.get("reason", "")).strip()[:1000],
        })
    return findings


def max_severity(findings: List[Dict[str, Any]]) -> Optional[str]:
    if not findings:
        return None
    return max((f["severity"] for f in findings), key=SEVERITY_RANK.__getitem__)


def check_endpoint(base_url: Optional[str], allowed_hosts) -> str:
    """Return the endpoint's host, or raise if it is not allowed.

    A client with no base URL talks to api.openai.com, which is exactly
    where this content must not go.
    """
    host = urlparse(str(base_url)).hostname if base_url else "api.openai.com"
    if host not in set(allowed_hosts):
        raise ValueError(
            f"refusing to send document text to {host}: not in the allowed hosts "
            f"({', '.join(sorted(allowed_hosts))}); pass --allow-endpoint {host} if it is local"
        )
    return host


def scan_text(
    client,
    model: str,
    text: str,
    *,
    title: str = "",
    source: str = "",
    source_type: str = "",
    window_chars: int = 24000,
    overlap_chars: int = 800,
    reasoning_effort: Optional[str] = "low",
    max_tokens: int = 4000,
    document_id: Optional[str] = None,
    raw_document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan one document's text, window by window.

    Returns a dict with `findings` (each tagged with its window and whether
    the evidence was found in it), `windows`, `prompt_tokens`,
    `completion_tokens` and `seconds`. Raises on the first window that fails
    (after retries for throttling), so a document is either fully scanned or
    recorded as an error.
    """
    windows = split_windows(text, window_chars, overlap_chars)
    findings: List[Dict[str, Any]] = []
    prompt_tokens = completion_tokens = 0
    start = time.time()

    extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else None
    for i, window_text in enumerate(windows, start=1):
        messages = build_messages(
            window_text, title=title, source=source, source_type=source_type,
            window=i, windows=len(windows),
        )
        response = call_with_backoff(
            client.chat.completions.create,
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        record_llm_usage(
            getattr(response, "usage", None),
            stage=STAGE_SENSITIVITY_SCAN,
            model=model,
            document_id=document_id,
            raw_document_id=raw_document_id,
            meta={"window": i, "windows": len(windows), "prompt_version": PROMPT_VERSION},
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens += usage.prompt_tokens or 0
            completion_tokens += usage.completion_tokens or 0

        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ValueError(f"window {i}/{len(windows)}: answer cut off at max_tokens={max_tokens}")
        for f in parse_findings(choice.message.content):
            f["window"] = i
            f["evidence_found"] = evidence_in_text(f["evidence"], window_text)
            findings.append(f)

    return {
        "findings": findings,
        "windows": len(windows),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "seconds": time.time() - start,
    }
