"""The privacy policy's rules, the hold on new documents, and how both interact with sweeps."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kb_mcp.kb import tools
from kb_mcp.kb.db_models import (
    Base, Document, DocumentSensitivityScan, DocumentVisibilityDecision, RawDocument, Source,
)
from kb_mcp.privacy.redaction import find_replacements


def f(category, severity, span="", evidence=None, source="llm"):
    return {"category": category, "severity": severity, "span": span,
            "evidence": evidence if evidence is not None else span, "reason": "r", "source": source}


def actions(findings, text):
    return tools.privacy_policy_actions(findings, text, find_replacements(text))


# --- rules -------------------------------------------------------------------------

def test_restriction_notice_hides_from_search_and_get():
    text = "Slide 4. General Atomics Proprietary Information"
    a = actions([f("restricted", "medium", "General Atomics Proprietary Information")], text)
    assert a["hide_search"] and a["hide_get"] and not a["redact"]


def test_restricted_without_notice_wording_is_only_flagged():
    text = "Data: https://drive.google.com/drive/folders/abc"
    a = actions([f("restricted", "medium", "https://drive.google.com/drive/folders/abc")], text)
    assert not a["hide_search"] and a["flag"]


def test_marker_findings_always_hide():
    text = "Excerpt labelled Internal Use Only"
    a = actions([f("restricted", "medium", "Internal Use Only", source="marker")], text)
    assert a["hide_get"]


def test_real_password_hides_and_redacts():
    text = "user tier3 password manager entry: TierIIIUser! (do not share)"
    a = actions([f("credentials", "high", "TierIIIUser!")], text)
    assert a["hide_search"] and a["hide_get"]
    assert [r["span"] for r in a["redact"]] == ["TierIIIUser!"]


def test_medium_credential_is_redacted_not_hidden():
    text = "Liebert unit lock code 1490, then open the panel"
    a = actions([f("credentials", "medium", "1490")], text)
    assert not a["hide_search"] and [r["label"] for r in a["redact"]] == ["credential"]


@pytest.mark.parametrize("span", ["??????", "", "zz"])
def test_credential_without_usable_span_is_flagged_not_hidden(span):
    text = "dial in, the code is ?????? see the slide"
    a = actions([f("credentials", "medium", span)], text)
    assert not a["hide_search"] and not a["hide_get"] and not a["redact"] and a["flag"]


def test_findings_covered_by_patterns_are_ignored():
    text = "Zoom passcode: mu2eops"
    assert actions([f("credentials", "medium", "Passcode: mu2eops")], text) == \
        {"hide_search": False, "hide_get": False, "redact": [], "flag": [], "reasons": []}


def test_high_personal_data_hides_from_search_and_redacts():
    text = "I am having a knee replacement on June 9th and will be out three weeks"
    a = actions([f("personal_data", "high", "knee replacement on June 9th")], text)
    assert a["hide_search"] and not a["hide_get"]
    assert a["redact"][0]["span"] == "knee replacement on June 9th"


def test_medium_personal_data_is_redacted_when_exact():
    text = "Email call or text 630 -803-5268 if you need me"
    a = actions([f("personal_data", "medium", "630-803-5268")], text)
    assert not a["hide_search"] and a["redact"][0]["label"] == "personal data"


def test_span_only_in_the_summary_is_redacted_like_one_in_the_text():
    # The scan reads title + summary + text; the policy checks spans against
    # that same composition, not the bare text, or this would only be flagged.
    scanned = tools._scanned_text("Shift notes", "Contact Jane at 630-803-5268 for access.", "Body text.")
    assert scanned.startswith("[Title] Shift notes\n[AI-generated summary] Contact Jane")
    assert scanned.endswith("[Document text]\nBody text.")
    a = actions([f("personal_data", "medium", "630-803-5268")], scanned)
    assert a["redact"] and not a["flag"]
    assert tools._scanned_text(None, None, "Body text.") == "Body text."


def test_paraphrased_personal_data_is_flagged():
    text = "Email call or text if you need me"
    a = actions([f("personal_data", "medium", "his mobile number")], text)
    assert not a["redact"] and a["flag"]


def test_low_and_security_findings_do_not_act():
    text = "host mu2e-dcs-01 at 192.168.157.26"
    a = actions([f("security", "medium", "192.168.157.26"), f("personal_data", "low", "mu2e-dcs-01")], text)
    assert not a["hide_search"] and not a["redact"] and a["flag"] == ["security: 192.168.157.26"]


def test_duplicate_spans_are_redacted_once():
    text = "call 630-803-5268 or 630-803-5268"
    a = actions([f("personal_data", "medium", "630-803-5268")] * 2, text)
    assert len(a["redact"]) == 1


# --- hold on new documents ---------------------------------------------------------------

def test_hold_is_off_by_default(monkeypatch):
    from kb_mcp.kb.documents import operations

    calls = []
    monkeypatch.setattr(tools, "record_visibility_decision", lambda *a, **k: calls.append(k))
    monkeypatch.delenv("PRIVACY_HOLD_NEW_DOCUMENTS", raising=False)
    operations._hold_for_privacy_scan("raw-1", session=None)
    assert calls == []


def test_hold_hides_new_documents_when_enabled(monkeypatch):
    from kb_mcp.kb.documents import operations

    calls = []
    monkeypatch.setattr(tools, "record_visibility_decision", lambda raw, **k: calls.append((raw, k)))
    monkeypatch.setenv("PRIVACY_HOLD_NEW_DOCUMENTS", "true")
    operations._hold_for_privacy_scan("raw-1", session="s")
    [(raw, kwargs)] = calls
    assert raw == "raw-1" and kwargs["visible_search"] is False and kwargs["visible_get"] is False
    assert kwargs["meta"]["origin"] == tools.PRIVACY_HOLD_ORIGIN and kwargs["session"] == "s"


# --- sweeps and scan selection --------------------------------------------------------

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/policy.db")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Source(id="mu2e-docdb"))
    for raw_id in ("r1", "r2", "r3"):
        s.add(RawDocument(id=raw_id, source_id="mu2e-docdb", source_type="application/pdf", content_hash=raw_id))
        s.add(Document(id=f"{raw_id}-text", source_id="mu2e-docdb", raw_document_id=raw_id,
                       source_type="application/pdf", doc_type="text", text="some text"))
    s.flush()
    yield s
    s.close()


def _decision(session, raw_id, seq, origin):
    session.add(DocumentVisibilityDecision(raw_document_id=raw_id, seq=seq, group_name="*",
                                           visible_search=False, visible_get=False, meta={"origin": origin}))
    session.flush()


def test_sweeps_do_not_count_automatic_decisions_as_reviewed(session):
    _decision(session, "r1", 1, tools.PRIVACY_HOLD_ORIGIN)
    _decision(session, "r2", 2, tools.PRIVACY_POLICY_ORIGIN)
    _decision(session, "r3", 3, "cli")
    assert tools._already_decided(session, {"r1", "r2", "r3"}, "*") == {"r3"}


def test_only_unscanned_skips_documents_scanned_by_any_version(session):
    session.add(DocumentSensitivityScan(document_id="r1-text", raw_document_id="r1", prompt_version="v3", status="ok"))
    session.add(DocumentSensitivityScan(document_id="r2-text", raw_document_id="r2", prompt_version="v3", status="error"))
    session.flush()
    current = tools.sensitivity_candidates(session, prompt_version="v4")
    assert sorted(c["document_id"] for c in current["candidates"]) == ["r1-text", "r2-text", "r3-text"]
    new_only = tools.sensitivity_candidates(session, prompt_version="v4", only_unscanned=True)
    assert sorted(c["document_id"] for c in new_only["candidates"]) == ["r2-text", "r3-text"]


def test_privacy_hold_hides_the_graph(monkeypatch):
    """The graph tools apply neither visibility nor redaction, so they must go."""
    from kb_mcp.config import get_server_config

    monkeypatch.delenv("HIDE_GRAPH", raising=False)
    monkeypatch.delenv("PRIVACY_HOLD_NEW_DOCUMENTS", raising=False)
    assert get_server_config()["hide_graph"] is False
    monkeypatch.setenv("PRIVACY_HOLD_NEW_DOCUMENTS", "true")
    assert get_server_config()["hide_graph"] is True
