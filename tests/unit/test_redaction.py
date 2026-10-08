"""Redaction of MCP output: global patterns, span rules, offsets, and the formatter."""

from types import SimpleNamespace

import pytest

from kb_mcp.privacy import redaction as r


@pytest.fixture(autouse=True)
def no_db_rules(monkeypatch):
    """Span rules come from the database; tests inject them through _load_rules."""
    rules = {"by_raw": {}, "global": []}
    monkeypatch.setattr(r, "_load_rules", lambda: (rules["by_raw"], rules["global"]))
    r.clear_cache()
    yield rules
    r.clear_cache()


def rule(span, raw_id="raw-1", label="personal data"):
    return r.SpanRule("rule-" + span, raw_id, span, label, r.span_regex(span))


# --- global patterns -------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("https://fnal.zoom.us/j/98220253597?pwd=RWlyQStRYmNub3gvT0VT - Meeting ID: 982 2025 3597",
     "https://fnal.zoom.us/j/98220253597?pwd=[redacted: meeting passcode] - Meeting ID: 982 2025 3597"),
    ("[https://fnal.zoom.us/j/98220253597?pwd=RWlyQStRYmNub3gvT0VT OWhLMFp6dz09](x) Meeting ID",
     "[https://fnal.zoom.us/j/98220253597?pwd=[redacted: meeting passcode]](x) Meeting ID"),
    ("?pwd=pN367ShOczQYcc8PSaq0Uz98T0qaJw.1 Meeting ID: 434",
     "?pwd=[redacted: meeting passcode] Meeting ID: 434"),
    ("Passcode: mu2eops - Phone passcode: 9682791#",
     "Passcode: [redacted: credential] - Phone passcode: [redacted: credential]"),
    ("Zoom: 849 878 3372 pw: 631200 After the meeting",
     "Zoom: 849 878 3372 pw: [redacted: credential] After the meeting"),
    ("Zoom passcode:  631200 and Password: MCR3721.",
     "Zoom passcode:  [redacted: credential] and Password: [redacted: credential]"),
    ("PRN279049 2 MAG SHIELD 5936272675 Card#SONYA7 | 6787 TranID3122547301",
     "PRN279049 2 MAG SHIELD 5936272675 [redacted: payment card] TranID3122547301"),
    ("token ghp_" + "a" * 36 + " end", "token [redacted: access token] end"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "[redacted: private key]"),
])
def test_global_patterns_redact(text, expected):
    assert r.redact(text) == expected


@pytest.mark.parametrize("text", [
    "Login with username and services account password.",
    "Password: see below",
    "password: <your password>",
    "Password: ****",
    "Meeting ID: 630 840 6691",
    "The pin assignment of the connector is in table 3.",
    "DocDB 56323, card reader test",
    "tau ~ 20-25 ns (decay) pw = shaping constant",
])
def test_global_patterns_leave_ordinary_text(text):
    assert r.redact(text) == text


def test_placeholders_are_not_redacted_again():
    once = r.redact("Passcode: mu2eops")
    assert r.redact(once) == once


# --- span rules ----------------------------------------------------------------

def test_span_rule_matches_across_whitespace_and_case():
    text = "Email call or text\n630 -803-5268 if you need me. JPECK607@gmail.com"
    out = r.redact(text, [rule("630 -803-5268"), rule("jpeck607@gmail.com")])
    assert out == "Email call or text\n[redacted: personal data] if you need me. [redacted: personal data]"


@pytest.mark.parametrize("variant", ["630-803-5268", "630 -803-5268", "(630) 803 5268", "6308035268", "630.803.5268"])
def test_phone_span_matches_any_separators(variant):
    assert r.redact(f"text {variant} if you need me", [rule("630-803-5268")]) == \
        "text [redacted: personal data] if you need me"


def test_phone_span_does_not_match_inside_longer_numbers():
    text = "TranID16308035268 and 46308035268"
    assert r.redact(text, [rule("630-803-5268")]) == text


def test_overlapping_rules_keep_one_placeholder():
    text = "Password: Tr4cker!2024 for the DAQ"
    out = r.redact(text, [rule("Tr4cker!2024", label="credential")])
    assert out == "Password: [redacted: credential] for the DAQ"


def test_span_must_not_be_empty():
    with pytest.raises(ValueError):
        r.span_regex("   ")


def test_rules_for_combines_document_and_global_rules(no_db_rules):
    no_db_rules["by_raw"]["raw-1"] = [rule("555-0100")]
    no_db_rules["global"].append(rule("hunter2-secret", raw_id=None))
    assert {x.span for x in r.rules_for("raw-1")} == {"555-0100", "hunter2-secret"}
    assert {x.span for x in r.rules_for("raw-2")} == {"hunter2-secret"}
    assert {x.span for x in r.rules_for(None)} == {"hunter2-secret"}


def test_rules_survive_a_failing_reload(monkeypatch, no_db_rules):
    no_db_rules["by_raw"]["raw-1"] = [rule("555-0100")]
    assert r.rules_for("raw-1")
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(r, "_load_rules", boom)
    monkeypatch.setattr(r, "_CACHE_SECONDS", -1)
    assert r.rules_for("raw-1")  # last good rules stay in use
    assert r.redact_output("Passcode: mu2eops") == "Passcode: [redacted: credential]"


# --- offsets -------------------------------------------------------------------

def test_offset_mapper_tracks_shifts_and_clamps_into_placeholders():
    text = "aaa Passcode: mu2eops bbb Card#SONYA7 6787 ccc"
    reps = r.find_replacements(text)
    out = r.apply_replacements(text, reps)
    to = r.offset_mapper(reps)
    assert out[to(0):to(3)] == "aaa"
    assert out[to(text.index("bbb")):to(text.index("bbb") + 3)] == "bbb"
    assert out[to(text.index("ccc")):] == "ccc"
    inside = text.index("mu2eops") + 3
    assert to(inside) == out.index("[redacted: credential]")
    to_end = r.offset_mapper(reps, end=True)
    assert to_end(inside) == out.index("[redacted: credential]") + len("[redacted: credential]")
    assert to_end(text.index("bbb")) == to(text.index("bbb"))


# --- MCP formatting --------------------------------------------------------------

def _doc(text, raw_id="raw-1", **kw):
    fields = dict(
        id="doc-1", source_id="mu2e-docdb", doc_id="1234", raw_document_id=raw_id,
        text=text, title="Ops news", title_gen=None, uri=None, doc_type="text",
        source_type="application/pdf", meta={}, summary=None,
    )
    fields.update(kw)
    return SimpleNamespace(**fields)


def test_search_excerpt_never_cuts_a_secret(no_db_rules):
    from kb_mcp.server.mcp import _format_search_results

    secret = "Passcode: mu2eops"
    text = "x" * 3000 + " " + secret + " " + "y" * 3000
    hit = text.index("mu2eops") + 4  # match chunk ends inside the passcode
    results = [{"document": _doc(text), "chunks": [{"char_start": hit - 50, "char_end": hit,
                                                    "chunk_strategy": "tokens_512_51", "text": "..."}]}]
    [out] = _format_search_results(results, context_chars=2)
    assert "mu2eops" not in out["text"] and "mu2e" not in out["text"].split("Excerpt")[-1]
    assert "[redacted: credential]" in out["text"]


def test_search_redacts_document_rules_in_summary_and_title(no_db_rules):
    from kb_mcp.server.mcp import _format_search_results

    no_db_rules["by_raw"]["raw-1"] = [rule("630-803-5268")]
    doc = _doc("short text, call 630-803-5268", title="Call 630-803-5268")
    [out] = _format_search_results([{"document": doc, "chunks": []}])
    assert "630-803-5268" not in out["text"] and "630-803-5268" not in out["title"]
    other = _doc("short text, call 630-803-5268", raw_id="raw-2")
    [out2] = _format_search_results([{"document": other, "chunks": []}])
    assert "630-803-5268" in out2["text"]  # a document rule stays with its document


def test_redacting_wrapper_keeps_signature_and_redacts(no_db_rules):
    import inspect
    from kb_mcp.server.mcp import _redacting

    def tool(query: str, max_results: int = 5) -> str:
        """Doc."""
        return f"{query}: Passcode: mu2eops"

    wrapped = _redacting(tool)
    assert inspect.signature(wrapped) == inspect.signature(tool) and wrapped.__doc__ == "Doc."
    assert wrapped("q") == "q: Passcode: [redacted: credential]"


@pytest.mark.asyncio
async def test_redacting_wrapper_handles_async_tools(no_db_rules):
    from kb_mcp.server.mcp import _redacting

    async def research(question: str) -> str:
        return "the passcode is Passcode: mu2eops"

    assert await _redacting(research)("q") == "the passcode is Passcode: [redacted: credential]"
