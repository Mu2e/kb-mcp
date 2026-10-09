"""The privacy review: reviewer actions and the /web/privacy page."""

from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import RedirectResponse
from starlette.testclient import TestClient

from kb_mcp.kb import tools
from kb_mcp.server.web.routes import privacy as privacy_routes


# --- reviewer actions ---------------------------------------------------------------

@pytest.fixture()
def recorded(monkeypatch):
    calls = {"decisions": [], "redactions": []}
    monkeypatch.setattr(tools, "record_visibility_decision",
                        lambda raw, **k: calls["decisions"].append((raw, k)) or {"applied": True})
    monkeypatch.setattr(tools, "add_redaction",
                        lambda span, **k: calls["redactions"].append((span, k)) or {"id": "r1", "added": True})
    return calls


@pytest.mark.parametrize("action, visible", [
    ("show", (True, True)), ("hide", (False, False)), ("hide-search", (False, True)),
])
def test_visibility_actions_are_final_review_decisions(recorded, action, visible):
    message = tools.privacy_review_action("raw-1", action, user="alice", reason="checked")
    [(raw, kwargs)] = recorded["decisions"]
    assert raw == "raw-1" and (kwargs["visible_search"], kwargs["visible_get"]) == visible
    assert kwargs["meta"] == {"origin": tools.PRIVACY_REVIEW_ORIGIN, "user": "alice"}
    assert kwargs["reason"] == "checked"
    assert tools.PRIVACY_REVIEW_ORIGIN not in tools.AUTO_VISIBILITY_ORIGINS  # the policy will not touch it
    assert message.startswith("set:")


def test_empty_reason_names_the_reviewer(recorded):
    tools.privacy_review_action("raw-1", "show", user="alice", reason="  ")
    assert recorded["decisions"][0][1]["reason"] == "reviewed by alice"


def test_redact_needs_a_span(recorded):
    with pytest.raises(ValueError, match="needs the text"):
        tools.privacy_review_action("raw-1", "redact", user="alice")
    assert tools.privacy_review_action("raw-1", "redact", user="alice", span="630-803-5268",
                                       label="personal data") == "redacted '630-803-5268'"
    span, kwargs = recorded["redactions"][0]
    assert kwargs["raw_document_id"] == "raw-1" and kwargs["created_by"] == "alice"


def test_unknown_action_is_refused(recorded):
    with pytest.raises(ValueError, match="unknown action"):
        tools.privacy_review_action("raw-1", "delete", user="alice")


# --- web page ---------------------------------------------------------------------------

QUEUE = {
    "held": [],
    "provisional": [{
        "raw_document_id": "raw-1", "label": "mu2e-docdb/50596-pw", "title": "DAQ <b>Ops</b>",
        "document_id": "doc-1", "redactions": 2,
        "state": {"search": False, "get": False, "origin": "privacy-policy",
                  "reason": "password or secret", "since": "2026-10-09 00:04"},
        "findings": [{"category": "credentials", "severity": "high", "subject": "rack",
                      "evidence": "<script>alert(1)</script> pw Al27", "span": "Al27Mu0nmu2edcs",
                      "reason": "a password", "covered": False}],
    }],
    "flagged": [],
}


@pytest.fixture()
def client(monkeypatch):
    actions = []
    state = {"admin": True}

    monkeypatch.setattr(tools, "privacy_review_queue", lambda: QUEUE)
    monkeypatch.setattr(tools, "privacy_review_action",
                        lambda raw, action, **k: actions.append((raw, action, k)) or f"did {action}")

    async def require_auth_html(request, session_manager, require_admin=False):
        if require_admin and not state["admin"]:
            return None, RedirectResponse("/login", status_code=303)
        return {"username": "alice"}, None

    async def require_admin(request, session_manager):
        return None if state["admin"] else RedirectResponse("/login", status_code=303)

    monkeypatch.setattr(privacy_routes, "require_admin", require_admin)

    async def get_session_username(request, force_reverify=False):
        return "alice"

    session_manager = SimpleNamespace(
        require_auth=False,
        get_auth_warning_html=lambda: "",
        get_session_username=get_session_username,
    )
    app = Starlette()
    privacy_routes.setup_privacy_routes(app, session_manager, require_auth_html)
    c = TestClient(app)
    c.actions, c.state = actions, state
    return c


def test_page_lists_the_queue_and_escapes_document_text(client):
    page = client.get("/web/privacy").text
    assert "mu2e-docdb/50596-pw" in page and "Hidden by the privacy policy (1)" in page
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
    assert "DAQ &lt;b&gt;Ops&lt;/b&gt;" in page
    assert "Confirm (keep as is)" in page and "Redact" in page


def test_decision_is_applied_by_the_reviewer_and_redirects_back(client):
    r = client.post("/web/privacy/decide", data={"raw_document_id": "raw-1", "action": "confirm",
                                                  "section": "provisional", "reason": "real password"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#provisional")
    assert "done=did+confirm" in r.headers["location"]
    [(raw, action, kwargs)] = client.actions
    assert (raw, action, kwargs["user"], kwargs["reason"]) == ("raw-1", "confirm", "alice", "real password")


def test_non_admins_can_neither_read_nor_decide(client):
    client.state["admin"] = False
    assert client.get("/web/privacy", follow_redirects=False).status_code == 303
    r = client.post("/web/privacy/decide", data={"raw_document_id": "raw-1", "action": "show"},
                    follow_redirects=False)
    assert r.status_code == 303 and client.actions == []
