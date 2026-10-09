"""Privacy review page (/web/privacy): decide about held, provisional and flagged documents.

The privacy pipeline (docs/guides/privacy.md) hides new documents until they
are scanned, and lets a policy hide or redact what the scan finds. Those
decisions are provisional; this page is where a person confirms or corrects
them. Admin only: it shows the sensitive passages themselves.
"""

import logging
from html import escape as html_escape
from urllib.parse import quote, urlencode

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from ..auth import WebSessionManager, require_admin
from .. import html_templates

logger = logging.getLogger(__name__)

SECTIONS = (
    ("held", "On hold",
     "New documents waiting for the import job's privacy scan. They clear themselves at the next run; "
     "one older than a day means the scan is not running."),
    ("provisional", "Hidden by the privacy policy",
     "Hidden automatically. Confirm to keep them hidden, or show them (redactions still apply)."),
    ("flagged", "Flagged",
     "Visible documents with findings that no redaction covers yet. Redact a passage, hide the document, "
     "or mark it reviewed with Show."),
)

REDACTION_LABELS = {
    "credentials": "credential",
    "personal_data": "personal data",
    "personnel": "personnel matter",
    "individual_assessment": "assessment of a person",
    "security": "security detail",
}


def _state_badge(state) -> str:
    if not state or (state["search"] and state["get"]):
        text, colour = "visible", "#2e7d32"
    elif not state["get"]:
        text, colour = "hidden from search and get", "#c62828"
    else:
        text, colour = "hidden from search", "#ef6c00"
    origin = f" · {html_escape(state['origin'])}" if state and state.get("origin") else ""
    since = f" · since {html_escape(state['since'])}" if state and state.get("since") else ""
    return f'<span style="color:{colour}; font-weight:bold;">{text}</span><span style="color:#777;">{origin}{since}</span>'


def _action_form(raw_id: str, action: str, label: str, section: str, style: str = "", extra: dict = None,
                 confirm: str = "") -> str:
    hidden = {"raw_document_id": raw_id, "action": action, "section": section, **(extra or {})}
    inputs = "".join(f'<input type="hidden" name="{k}" value="{html_escape(str(v))}">' for k, v in hidden.items())
    onclick = f' onclick="return confirm({html_escape(repr(confirm))})"' if confirm else ""
    # The reason box lives outside the small forms; copy it in on submit.
    return (f'<form method="POST" action="/web/privacy/decide" style="display:inline" '
            f'onsubmit="this.reason.value=document.getElementById(\'reason-{html_escape(raw_id)}\').value">'
            f'{inputs}<input type="hidden" name="reason" value="">'
            f'<button type="submit" class="btn" style="{style}"{onclick}>{html_escape(label)}</button></form>')


def _document_card(doc: dict, section: str) -> str:
    raw_id = doc["raw_document_id"]
    link = (f'<a href="/web/document/{html_escape(doc["document_id"])}">{html_escape(doc["label"])}</a>'
            if doc.get("document_id") else html_escape(doc["label"]))
    state = doc.get("state")
    why = ""
    if state and state.get("reason") and section != "flagged":
        why = f'<div style="color:#555; margin:4px 0;">Why: {html_escape(state["reason"][:400])}</div>'

    findings_html = []
    for f in doc["findings"]:
        quote_text = f.get("evidence") or ""
        span = " ".join((f.get("span") or "").split())
        if f["covered"]:
            status = '<span style="color:#2e7d32;">redacted</span>'
            button = ""
        else:
            status = html_escape(f.get("severity") or "")
            button = ""
            if span and f.get("category") != "restricted" and len(span) >= 4:
                button = _action_form(
                    raw_id, "redact", f"Redact “{span[:40]}”", section,
                    extra={"span": span, "label": REDACTION_LABELS.get(f.get("category"), "sensitive detail")},
                )
        source = " · pattern" if f.get("source") == "marker" else ""
        findings_html.append(
            "<li style='margin-bottom:6px;'>"
            f"<b>[{status}] {html_escape(f.get('category') or '')}</b>{source}: "
            f"{html_escape(f.get('subject') or '')}<br>"
            f'<span style="font-family:monospace; background:#f6f6f6;">“{html_escape(quote_text[:300])}”</span>'
            + (f"<br><span style='color:#555;'>{html_escape((f.get('reason') or '')[:200])}</span>" if f.get("reason") else "")
            + (f"<br>{button}" if button else "")
            + "</li>"
        )
    findings_block = f"<ul style='margin:6px 0 6px 18px; padding:0;'>{''.join(findings_html)}</ul>" if findings_html else \
        "<div style='color:#777;'>No findings (not scanned yet, or nothing above low severity).</div>"

    buttons = []
    if section == "provisional":
        buttons.append(_action_form(raw_id, "confirm", "Confirm (keep as is)", section, "background:#1565c0;"))
    buttons.append(_action_form(raw_id, "show", "Show", section, "background:#2e7d32;",
                                confirm="Make this document visible to the MCP tools (redactions still apply)?"))
    buttons.append(_action_form(raw_id, "hide-search", "Hide from search", section, "background:#ef6c00;"))
    buttons.append(_action_form(raw_id, "hide", "Hide from search and get", section, "background:#c62828;"))

    return f"""
    <div class="card" id="d-{html_escape(raw_id)}" style="margin-bottom:12px;">
        <div style="font-size:15px;"><b>{link}</b> — {html_escape(doc.get('title') or '')}</div>
        <div style="margin:2px 0;">{_state_badge(state)} · {doc.get('redactions', 0)} redaction rule(s)
            · <code style="font-size:11px;">kb tools visibility preview {html_escape(raw_id)}</code></div>
        {why}
        {findings_block}
        <div style="margin-top:6px;">
            <input id="reason-{html_escape(raw_id)}" type="text" placeholder="Reason (optional)" style="width:280px;">
            {' '.join(buttons)}
        </div>
    </div>"""


def setup_privacy_routes(app, session_manager: WebSessionManager, require_auth_html):
    """Register the privacy review page and its decision endpoint (admin only)."""
    from ....kb.tools import privacy_review_action, privacy_review_queue

    async def web_privacy(request: Request):
        session_data, redirect = await require_auth_html(request, session_manager, require_admin=True)
        if redirect:
            return redirect
        username = session_data.get("username")
        queue = await run_in_threadpool(privacy_review_queue)

        done = request.query_params.get("done")
        error = request.query_params.get("error")
        notice = ""
        if done:
            notice = f'<div class="success-box">{html_escape(done)}</div>'
        if error:
            notice = f'<div class="error-box">{html_escape(error)}</div>'

        summary = " · ".join(f'<a href="#{key}">{title}: {len(queue[key])}</a>' for key, title, _ in SECTIONS)
        body = []
        for key, title, help_text in SECTIONS:
            docs = queue[key]
            cards = "".join(_document_card(d, key) for d in docs) or "<p>Nothing here.</p>"
            body.append(f'<h2 id="{key}" style="margin-top:24px;">{title} ({len(docs)})</h2>'
                        f'<p style="color:#555;">{help_text}</p>{cards}')

        content = f"""
        {session_manager.get_auth_warning_html()}
        <div class="card">
            <h1>Privacy Review</h1>
            <p>Documents the MCP tools (and so external AI clients) are kept from, and documents that may need it.
            Decisions here are final for the privacy policy; the CLI and this web UI always see everything.
            See <code>docs/guides/privacy.md</code>.</p>
            <p>{summary}</p>
            {notice}
        </div>
        {''.join(body)}
        """
        return HTMLResponse(html_templates.base_template("Privacy Review", content, None, username))

    async def web_privacy_decide(request: Request):
        # Re-verify for every write, as the admin pages do.
        guard = await require_admin(request, session_manager)
        if guard is not None:
            return guard
        if session_manager.require_auth and not await session_manager.has_admin_access(request, force_reverify=True):
            return RedirectResponse(url="/login?redirect=/web/privacy", status_code=303)
        username = await session_manager.get_session_username(request, force_reverify=False) or "web-admin"

        form = await request.form()
        raw_id = (form.get("raw_document_id") or "").strip()
        action = (form.get("action") or "").strip()
        section = (form.get("section") or "").strip()
        anchor = f"#{quote(section)}" if section in {k for k, _, _ in SECTIONS} else ""
        try:
            message = await run_in_threadpool(
                privacy_review_action, raw_id, action,
                user=username,
                reason=form.get("reason"),
                span=form.get("span"),
                label=form.get("label"),
                rule_id=form.get("rule_id"),
            )
            logger.info("privacy review by %s: %s %s -> %s", username, action, raw_id, message)
            params = {"done": message}
        except ValueError as e:
            params = {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - shown on the page, logged in full
            logger.error("privacy review action failed: %s", e, exc_info=True)
            params = {"error": f"failed: {e}"}
        return RedirectResponse(url=f"/web/privacy?{urlencode(params)}{anchor}", status_code=303)

    app.add_route("/web/privacy", web_privacy, methods=["GET"])
    app.add_route("/web/privacy/decide", web_privacy_decide, methods=["POST"])
