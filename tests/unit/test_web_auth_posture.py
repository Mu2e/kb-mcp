"""The web UI's auth posture, and whether it reports itself honestly.

WEB_REQUIRE_AUTH=false covers three configurations with very different
consequences, and the operator only ever learns which one they are in from a
log line and a banner. These tests pin what each one says, because a warning
that fires on a correctly configured deployment trains people to ignore the
one case that matters, and a banner that understates the exposure is worse
than no banner at all.
"""

import logging

import pytest

from kb_mcp.server.web.auth import WebSessionManager


@pytest.fixture
def posture(monkeypatch):
    """Build a WebSessionManager under an explicit auth configuration."""

    def _build(require_auth, public_mode, admin_password=None, caplog=None):
        monkeypatch.setenv("WEB_REQUIRE_AUTH", "true" if require_auth else "false")
        monkeypatch.setenv("WEB_PUBLIC_MODE", "true" if public_mode else "false")
        monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
        if admin_password:
            monkeypatch.setenv("ADMIN_PASSWORD", admin_password)
        else:
            monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        return WebSessionManager(oauth_provider=None)

    return _build


def _records(caplog):
    return [r for r in caplog.records if r.name.endswith("web.auth")]


def test_public_mode_with_password_does_not_warn(posture, caplog):
    """The intended Mu2e configuration. Browsing is meant to be open.

    This is the case that a blanket "authentication disabled" warning got
    wrong: it fired on every start of a correctly configured deployment.
    """
    with caplog.at_level(logging.INFO):
        manager = posture(require_auth=False, public_mode=True, admin_password="s3cret")

    assert manager.get_auth_warning_html() == ""
    warnings = [r for r in _records(caplog) if r.levelno >= logging.WARNING]
    assert warnings == [], f"unexpected warning: {[r.message for r in warnings]}"
    assert any("public mode" in r.message.lower() for r in _records(caplog))


def test_public_mode_without_password_says_the_write_pages_are_open(posture, caplog):
    """The banner must not claim the admin pages are unreachable.

    is_admin_unlocked() returns True when no password is configured, and
    require_admin() short-circuits on it, so every write route is open. A
    banner saying they "cannot be reached" describes the opposite situation.
    """
    with caplog.at_level(logging.INFO):
        manager = posture(require_auth=False, public_mode=True, admin_password=None)

    banner = manager.get_auth_warning_html()
    assert banner, "a deployment with unprotected write pages must say so"
    assert "cannot be reached" not in banner
    assert "reachable" in banner.lower()

    warnings = [r for r in _records(caplog) if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "ADMIN_PASSWORD" in warnings[0].message


@pytest.mark.asyncio
async def test_no_password_really_does_open_the_admin_gate(posture):
    """Pins the behaviour the banner above has to describe.

    If this ever becomes False, the banner and the startup warning are both
    wrong and must change with it.
    """
    manager = posture(require_auth=False, public_mode=True, admin_password=None)
    assert await manager.is_admin_unlocked(request=None) is True


def test_neither_flag_set_warns_about_silent_admin_sessions(posture, caplog):
    """WEB_REQUIRE_AUTH=false with WEB_PUBLIC_MODE=false is the dangerous one.

    /login mints a session carrying has_admin=True with no password and no
    check, so anyone who reaches the port gets the write pages.
    """
    with caplog.at_level(logging.INFO):
        manager = posture(require_auth=False, public_mode=False, admin_password="s3cret")

    warnings = [r for r in _records(caplog) if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "WEB AUTHENTICATION DISABLED" in warnings[0].message
    assert "WEB_PUBLIC_MODE" in warnings[0].message


def test_require_auth_is_silent(posture, caplog):
    with caplog.at_level(logging.INFO):
        manager = posture(require_auth=True, public_mode=False, admin_password="s3cret")

    assert manager.get_auth_warning_html() == ""
    assert [r for r in _records(caplog) if r.levelno >= logging.WARNING] == []
