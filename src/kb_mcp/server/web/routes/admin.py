"""Admin web interface for managing API keys."""

import logging
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse

from ..auth import WebSessionManager
from ...oauth import ApiKeyManager
from .. import html_templates
from ....config import get_api_keys_file
from ....alcf_auth import get_token_status, refresh_alcf_token, AlcfAuthError

logger = logging.getLogger(__name__)


def setup_admin_routes(app, oauth_provider, session_manager: WebSessionManager):
    """Setup admin web interface routes."""
    # Load API key manager directly (API keys are always available, even without OAuth)
    api_key_manager = ApiKeyManager(get_api_keys_file())

    async def admin_page(request):
        """Admin interface (OAuth protected, requires admin permissions)."""
        # Force re-verification on every admin page load for maximum security
        if not await session_manager.has_admin_access(request, force_reverify=True):
            return RedirectResponse(url="/login?redirect=/admin", status_code=303)

        # Get username from session (already verified above)
        username = await session_manager.get_session_username(
            request, force_reverify=False
        )

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get all API keys (dict mapping key -> info)
        keys = api_key_manager.list_keys()
        keys_html = ""
        for api_key, key_info in keys.items():
            # Truncate API key for display
            display_key = api_key[:12] + "..." if len(api_key) > 12 else api_key
            keys_html += f"""
            <tr>
                <td><code>{display_key}</code></td>
                <td>{key_info['username']}</td>
                <td>{key_info['description']}</td>
                <td>{key_info.get('created', 'N/A')}</td>
                <td>
                    <form method="POST" action="/admin/revoke" style="display:inline">
                        <input type="hidden" name="api_key" value="{api_key}">
                        <button type="submit" class="btn danger" onclick="return confirm('Revoke key for {key_info['username']}?')">Revoke</button>
                    </form>
                </td>
            </tr>
            """

        alcf_status = get_token_status()
        alcf_status_html = (
            f"Base URL: <code>{alcf_status['current_base_url'] or 'not set'}</code><br>"
            f"API key set: <strong>{'yes' if alcf_status['current_api_key_set'] else 'no'}</strong><br>"
            f"Stored ALCF login: <strong>{'found' if alcf_status['has_token_file'] else 'not found'}</strong>"
        )

        content = f"""
        <h1>API Key Management</h1>
        <p>Authenticated as: <strong>{username}</strong></p>

        {auth_warning}

        <div class="card">
            <h2>ALCF Inference Token</h2>
            <p style="font-size: 13px; color: #666;">
                Refreshes the ALCF access token using the stored Globus login (no browser needed).
                If there is no stored login, or it has fully expired, this will fail and you'll need to
                run <code>./scripts/setup_alcf.sh</code> in a terminal on the server instead.
            </p>
            <p id="alcf-status">{alcf_status_html}</p>
            <div style="display: flex; gap: 8px; align-items: center;">
                <label for="alcf-cluster">Cluster:</label>
                <select id="alcf-cluster">
                    <option value="sophia" selected>sophia</option>
                    <option value="metis">metis</option>
                </select>
                <button type="button" class="btn" id="alcf-refresh-btn" onclick="refreshAlcfToken()">Refresh ALCF Token</button>
            </div>
            <p id="alcf-result" style="margin-top: 10px; font-size: 13px;"></p>
        </div>
        <script>
            async function refreshAlcfToken() {{
                const btn = document.getElementById('alcf-refresh-btn');
                const resultEl = document.getElementById('alcf-result');
                const cluster = document.getElementById('alcf-cluster').value;
                btn.disabled = true;
                btn.textContent = 'Refreshing...';
                resultEl.style.color = '#666';
                resultEl.textContent = '';
                try {{
                    const response = await fetch('/admin/alcf/refresh', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{cluster}})
                    }});
                    const data = await response.json();
                    if (response.ok) {{
                        resultEl.style.color = '#2e7d32';
                        resultEl.textContent = `Success: base URL set to ${{data.base_url}}`;
                        document.getElementById('alcf-status').innerHTML =
                            `Base URL: <code>${{data.base_url}}</code><br>API key set: <strong>yes</strong><br>Stored ALCF login: <strong>found</strong>`;
                    }} else {{
                        resultEl.style.color = '#c62828';
                        resultEl.textContent = data.error || 'Refresh failed.';
                    }}
                }} catch (e) {{
                    resultEl.style.color = '#c62828';
                    resultEl.textContent = 'Request failed: ' + e;
                }} finally {{
                    btn.disabled = false;
                    btn.textContent = 'Refresh ALCF Token';
                }}
            }}
        </script>

        <div class="card">
            <h2>Generate New API Key</h2>
            <form method="POST" action="/admin/generate">
                <div class="form-group">
                    <label>Username: <input type="text" name="username" required></label>
                </div>
                <div class="form-group">
                    <label>Description: <input type="text" name="description" required></label>
                </div>
                <button type="submit" class="btn">Generate Key</button>
            </form>
        </div>

        <div class="card">
            <h2>Existing API Keys</h2>
            <table>
                <tr>
                    <th>API Key</th>
                    <th>Username</th>
                    <th>Description</th>
                    <th>Created</th>
                    <th>Actions</th>
                </tr>
                {keys_html}
            </table>
        </div>

        <p><a href="/">← Back to Home</a></p>
        """

        return HTMLResponse(html_templates.base_template(
            "Admin - API Key Management",
            content,
            None,
            username
        ))

    app.add_route("/admin", admin_page)

    async def admin_generate(request):
        """Generate API key."""
        # Force re-verification for sensitive admin operation
        if not await session_manager.has_admin_access(request, force_reverify=True):
            return RedirectResponse(url="/login?redirect=/admin", status_code=303)

        # Get username for logging (already verified above)
        username = await session_manager.get_session_username(
            request, force_reverify=False
        )

        form = await request.form()
        key_username = form.get("username")
        description = form.get("description", "")

        if not key_username:
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    '<div class="error-box"><h2>Error</h2><p>Missing username</p><p><a href="/admin">Back to Admin</a></p></div>',
                    None,
                    username
                ),
                status_code=400
            )

        try:
            api_key = api_key_manager.create_key(key_username, description)
            logger.info(f"Generated API key for {key_username} by {username}")

            content = f"""
            <h1>API Key Generated</h1>
            <div class="success-box">
                <p><strong>Username:</strong> {key_username}</p>
                <p><strong>Description:</strong> {description}</p>
                <p><strong>API Key:</strong> <code style="background: #f5f5f5; padding: 5px; display: block; margin: 10px 0;">{api_key}</code></p>
                <p style="color: #dc3545;"><strong>Important:</strong> Save this key now. It cannot be retrieved later.</p>
            </div>
            <p><a href="/admin" class="btn">Back to Admin</a></p>
            """

            return HTMLResponse(html_templates.base_template(
                "API Key Generated",
                content,
                None,
                username
            ))
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{str(e)}</p><p><a href="/admin">Back to Admin</a></p></div>',
                    None,
                    username
                ),
                status_code=400
            )

    app.add_route("/admin/generate", admin_generate, methods=["POST"])

    async def admin_revoke(request):
        """Revoke API key."""
        # Force re-verification for sensitive admin operation
        if not await session_manager.has_admin_access(request, force_reverify=True):
            return RedirectResponse(url="/login?redirect=/admin", status_code=303)

        # Get username for logging (already verified above)
        username = await session_manager.get_session_username(
            request, force_reverify=False
        )

        form = await request.form()
        api_key = form.get("api_key")

        if not api_key:
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    '<div class="error-box"><h2>Error</h2><p>Missing api_key</p><p><a href="/admin">Back to Admin</a></p></div>',
                    [("/", "Home"), ("/admin", "Admin")],
                    username
                ),
                status_code=400
            )

        try:
            success = api_key_manager.revoke_key(api_key)
            if success:
                logger.info(f"Revoked API key {api_key[:12]}... by {username}")
                return RedirectResponse(url="/admin", status_code=303)
            else:
                return HTMLResponse(
                    html_templates.base_template(
                        "Error",
                        '<div class="error-box"><h2>Error</h2><p>API key not found</p><p><a href="/admin">Back to Admin</a></p></div>',
                        [("/", "Home"), ("/admin", "Admin")],
                        username
                    ),
                    status_code=404
                )
        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return HTMLResponse(
                html_templates.base_template(
                    "Error",
                    f'<div class="error-box"><h2>Error</h2><p>{str(e)}</p><p><a href="/admin">Back to Admin</a></p></div>',
                    None,
                    username
                ),
                status_code=400
            )

    app.add_route("/admin/revoke", admin_revoke, methods=["POST"])

    async def admin_alcf_refresh(request):
        """Refresh the ALCF inference token (silent refresh only, no browser login)."""
        if not await session_manager.has_admin_access(request, force_reverify=True):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)

        username = await session_manager.get_session_username(
            request, force_reverify=False
        )

        try:
            body = await request.json()
        except Exception:
            body = {}
        cluster = body.get("cluster", "sophia")

        try:
            result = refresh_alcf_token(cluster)
            logger.info(f"Refreshed ALCF token ({cluster}) by {username}")
            return JSONResponse(result)
        except AlcfAuthError as e:
            logger.warning(f"ALCF token refresh failed for {username}: {e}")
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            logger.error(f"Unexpected error refreshing ALCF token: {e}")
            return JSONResponse({"error": f"Unexpected error: {e}"}, status_code=500)

    app.add_route("/admin/alcf/refresh", admin_alcf_refresh, methods=["POST"])

