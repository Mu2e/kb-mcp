"""Admin web interface for managing API keys (server package)."""

import logging
from starlette.responses import HTMLResponse, RedirectResponse

from .web_auth import WebSessionManager
from . import html_templates

logger = logging.getLogger(__name__)


def setup_admin_routes(app, oauth_provider, session_manager: WebSessionManager):
    """Setup admin web interface routes."""
    api_key_manager = oauth_provider.api_key_manager

    @app.route("/admin")
    async def admin_page(request):
        """Admin interface (GitHub OAuth protected, requires admin permissions)."""
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

        content = f"""
        <h1>API Key Management</h1>
        <p>Authenticated as: <strong>{username}</strong></p>

        {auth_warning}

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

    @app.route("/admin/generate", methods=["POST"])
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

    @app.route("/admin/revoke", methods=["POST"])
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

