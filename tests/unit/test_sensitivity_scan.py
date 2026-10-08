"""The LLM sensitivity scan's pure parts, and its candidate selection against SQLite.

The LLM calls themselves are not tested here; scan_text is exercised with a
fake client that returns canned answers.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb.db_models import Base, Document, DocumentSensitivityScan, RawDocument, Source
from kb_mcp.kb.tools import sensitivity_candidates
from kb_mcp.privacy import sensitivity


# --- windows -----------------------------------------------------------------

def test_short_text_is_one_window():
    assert sensitivity.split_windows("abc", window_chars=100, overlap_chars=10) == ["abc"]


def test_windows_cover_the_text_with_overlap():
    text = "".join(f"line {i:05d}\n" for i in range(5000))
    windows = sensitivity.split_windows(text, window_chars=2000, overlap_chars=200)
    assert len(windows) > 1
    assert all(len(w) <= 2000 for w in windows)
    # Every line appears whole in some window.
    for i in range(0, 5000, 37):
        assert any(f"line {i:05d}\n" in w for w in windows)
    # Windows cut at line breaks and overlap their neighbour.
    for a, b in zip(windows, windows[1:]):
        assert a.endswith("\n") or a is windows[-1]
        assert a[-200:] == b[:200]


def test_windows_without_line_breaks_cut_hard():
    windows = sensitivity.split_windows("x" * 5000, window_chars=1000, overlap_chars=100)
    assert "".join(w[100:] if i else w for i, w in enumerate(windows)) == "x" * 5000


@pytest.mark.parametrize("window_chars", [10, 900, 1001])
def test_window_must_leave_room_after_the_overlap(window_chars):
    with pytest.raises(ValueError):
        sensitivity.split_windows("x" * 5000, window_chars=window_chars, overlap_chars=800)


def test_smallest_valid_window_terminates():
    text = "\n".join("line %05d" % i for i in range(2000))
    windows = sensitivity.split_windows(text, window_chars=1002, overlap_chars=800)
    assert windows[-1].endswith("line 01999")


# --- parsing -----------------------------------------------------------------

def test_parse_empty_findings():
    assert sensitivity.parse_findings('{"findings": []}') == []


def test_parse_normalises_category_and_severity():
    content = """```json
    {"findings": [
      {"category": "Credentials", "severity": "HIGH", "subject": "DAQ account",
       "evidence": "password Tr4cker", "reason": "a password"},
      {"category": "gossip", "severity": "urgent", "evidence": "x"},
      "not a dict"
    ]}
    ```"""
    findings = sensitivity.parse_findings(content)
    assert [(f["category"], f["severity"]) for f in findings] == [
        ("credentials", "high"),
        ("other", "medium"),
    ]
    assert findings[0]["subject"] == "DAQ account"
    assert findings[0]["source"] == "llm" and findings[0]["span"] == ""


def test_parse_finds_json_after_preamble():
    assert sensitivity.parse_findings('Sure: {"findings": []}') == []


@pytest.mark.parametrize("content", ["", "no json here", '{"answer": 1}'])
def test_parse_rejects_answers_without_findings(content):
    with pytest.raises(ValueError):
        sensitivity.parse_findings(content)


def test_max_severity():
    assert sensitivity.max_severity([]) is None
    assert sensitivity.max_severity([{"severity": "low"}, {"severity": "high"}, {"severity": "medium"}]) == "high"


# --- evidence ----------------------------------------------------------------

def test_evidence_found_ignores_case_and_whitespace():
    text = "The DAQ login is\n  daqadmin / Password Tr4cker!2024."
    assert sensitivity.evidence_in_text("daqadmin /   password tr4cker!2024", text)
    assert not sensitivity.evidence_in_text("rootpw hunter2", text)
    assert not sensitivity.evidence_in_text("", text)


def test_stitched_evidence_needs_every_piece():
    text = "Zoom passcode: mu2eops. Later on the page: Password: MCR3721 for the AD meeting."
    assert sensitivity.evidence_in_text("Zoom passcode: mu2eops ... Password: MCR3721", text)
    assert sensitivity.evidence_in_text("passcode: mu2eops \u2026 MCR3721", text)
    assert not sensitivity.evidence_in_text("Zoom passcode: mu2eops ... Password: hunter2", text)


def test_long_evidence_matches_on_a_stretch():
    text = "A" * 50 + " the committee found the candidate's work unconvincing and recommends against hiring " + "B" * 50
    quote = "the committee found the candidate's work unconvincing and recommends against hiring him at all"
    assert sensitivity.evidence_in_text(quote, text)


# --- markers -----------------------------------------------------------------

def test_markers_find_restriction_notices_once_each():
    text = (
        "Slide 3\nGeneral Atomics Proprietary Information\nSlide 4\nGeneral Atomics Proprietary Information\n"
        "THIS DRAWING CONTAINS INFORMATION THAT IS PROPRIETARY TO MOLEX INC\n"
        "Official Use Only\nCUI//PROPIN\nECCN: 3A001\nMu2e internal, not for public distribution\n"
        "Excerpt from document labelled Internal Use Only\n"
    )
    found = {(f["subject"], f["severity"], f["span"]) for f in sensitivity.find_markers(text)}
    assert found == {
        ("proprietary notice", "medium", "Proprietary Information"),
        ("proprietary notice", "medium", "CONTAINS INFORMATION THAT IS PROPRIETARY"),
        ("official use only", "high", "Official Use Only"),
        ("CUI banner", "high", "CUI//PROPIN"),
        ("export control", "high", "ECCN: 3A001"),
        ("confidentiality notice", "medium", "not for public distribution"),
        ("confidentiality notice", "medium", "Internal Use Only"),
    }
    assert all(f["source"] == "marker" and f["category"] == "restricted" for f in sensitivity.find_markers(text))


@pytest.mark.parametrize("text", [
    "The braze alloy is proprietary, but non-magnetic.",
    "Open-weight vs proprietary (closed) models.",
    "Export Control Classification Number (ECCN) | EAR99 |",
    "A training slide: Identifying CUI. CUI applies to federally owned data.",
    "This e-mail may contain confidential information.",
])
def test_markers_ignore_ordinary_mentions(text):
    assert sensitivity.find_markers(text) == []


# --- endpoint guard ----------------------------------------------------------

def test_endpoint_allowed():
    assert sensitivity.check_endpoint("https://vllm.fnal.gov/v1/", sensitivity.DEFAULT_ALLOWED_HOSTS) == "vllm.fnal.gov"


@pytest.mark.parametrize("url", ["https://api.openai.com/v1/", "http://127.0.0.1:8080/v1", None])
def test_endpoint_refused(url):
    with pytest.raises(ValueError, match="refusing"):
        sensitivity.check_endpoint(url, sensitivity.DEFAULT_ALLOWED_HOSTS)


# --- prompt ------------------------------------------------------------------

def test_prompt_carries_text_context_and_categories():
    messages = sensitivity.build_messages(
        "SECRET-TEXT", title="Tracker minutes", source="mu2e-docdb/1234",
        source_type="application/pdf", window=2, windows=3,
    )
    user = messages[-1]["content"]
    assert "SECRET-TEXT" in user and "Tracker minutes" in user and "mu2e-docdb/1234" in user
    assert "Part 2 of 3" in user
    for category in sensitivity.CATEGORIES:
        assert category in user


# --- scan_text with a fake client ---------------------------------------------

class _FakeClient:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content, finish = self.answers.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110),
        )


@pytest.fixture()
def no_usage_writes(monkeypatch):
    monkeypatch.setattr(sensitivity, "record_llm_usage", lambda *a, **k: None)


def test_scan_text_tags_findings_with_window(no_usage_writes):
    text = "a\n" * 1500 + "login root password hunter2\n" + "b\n" * 10
    client = _FakeClient([
        ('{"findings": []}', "stop"),
        ('{"findings": [{"category": "credentials", "severity": "high", '
         '"evidence": "password hunter2", "reason": "r"}]}', "stop"),
    ])
    result = sensitivity.scan_text(client, "m", text, window_chars=3000, overlap_chars=100,
                                   reasoning_effort="low")
    assert result["windows"] == 2
    assert result["prompt_tokens"] == 200
    [f] = result["findings"]
    assert f["window"] == 2 and f["evidence_found"]
    assert f["source"] == "llm"


def test_scan_text_adds_markers_over_the_whole_text(no_usage_writes):
    text = "a\n" * 1500 + "General Atomics Proprietary Information\n"
    client = _FakeClient([('{"findings": []}', "stop"), ('{"findings": []}', "stop")])
    result = sensitivity.scan_text(client, "m", text, window_chars=3000, overlap_chars=100)
    [f] = result["findings"]
    assert f["source"] == "marker" and f["window"] is None
    assert client.calls[0]["extra_body"] == {"reasoning_effort": "low"}


def test_scan_text_fails_on_a_second_cut_off_answer(no_usage_writes):
    client = _FakeClient([('{"findings": [', "length"), ('{"findings": [', "length")])
    with pytest.raises(ValueError, match="cut off"):
        sensitivity.scan_text(client, "m", "text", reasoning_effort=None)
    assert len(client.calls) == 2
    assert client.calls[0]["extra_body"] is None


def test_scan_text_retries_an_empty_answer_once(no_usage_writes):
    client = _FakeClient([("", "stop"), ('{"findings": []}', "stop")])
    result = sensitivity.scan_text(client, "m", "text")
    assert result["findings"] == [] and len(client.calls) == 2
    assert result["prompt_tokens"] == 200  # both attempts counted


# --- candidate selection -------------------------------------------------------

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/scan.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Source(id="mu2e-docdb"))
    for raw_id in ("r1", "r2", "r3"):
        s.add(RawDocument(id=raw_id, source_id="mu2e-docdb", source_type="application/pdf", content_hash=raw_id))
    docs = [
        ("text-small", "r1", "text", "x" * 100),
        ("image-1", "r1", "image", "an image description"),
        ("table-1", "r1", "table", "| a | b |"),
        ("text-big", "r2", "text", "y" * 2000),
        ("text-empty", "r3", "text", ""),
        ("text-done", "r3", "text", "z" * 50),
    ]
    for doc_id, raw_id, doc_type, text in docs:
        s.add(Document(id=doc_id, source_id="mu2e-docdb", raw_document_id=raw_id,
                       source_type="application/pdf", doc_type=doc_type, text=text))
    s.add(DocumentSensitivityScan(document_id="text-done", raw_document_id="r3", prompt_version="v1", status="ok"))
    s.add(DocumentSensitivityScan(document_id="text-small", raw_document_id="r1", prompt_version="v1", status="error"))
    s.flush()
    yield s
    s.close()


def test_candidates_are_unscanned_text_documents_within_size(session):
    sel = sensitivity_candidates(session, prompt_version="v1", max_chars=1000)
    assert [c["document_id"] for c in sel["candidates"]] == ["text-small"]  # a failed scan is retried
    assert sel["already_scanned"] == 1
    assert sel["too_large"] == 1


def test_rescan_and_new_prompt_version_include_scanned_documents(session):
    for kwargs in ({"prompt_version": "v1", "rescan": True}, {"prompt_version": "v2"}):
        sel = sensitivity_candidates(session, max_chars=10_000, **kwargs)
        assert sorted(c["document_id"] for c in sel["candidates"]) == ["text-big", "text-done", "text-small"]


def test_candidates_restricted_to_document_ids(session):
    sel = sensitivity_candidates(session, prompt_version="v2", document_ids=["text-big", "image-1"])
    assert [c["document_id"] for c in sel["candidates"]] == ["text-big"]
