"""Admin web interface for managing API keys (server package)."""

import logging
from starlette.responses import HTMLResponse, RedirectResponse

from .web_auth import WebSessionManager

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
                        <button type="submit" onclick="return confirm('Revoke key for {key_info['username']}?')">Revoke</button>
                    </form>
                </td>
            </tr>
            """

        return HTMLResponse(
            f"""
<!DOCTYPE html>
<html>
<head>
    <title>Admin - API Key Management</title>
    <style>
        body {{ font-family: sans-serif; max-width: 800px; margin: 50px auto; padding: 0 20px; }}
        h1 {{ color: #333; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f5f5f5; }}
        button {{ padding: 5px 10px; cursor: pointer; }}
        .form-group {{ margin: 10px 0; }}
        input[type="text"] {{ width: 300px; padding: 5px; }}
        .logout {{ float: right; }}
    </style>
</head>
<body>
    <h1>API Key Management <a href="/logout" class="logout">Logout</a></h1>
    <p>Authenticated as: <strong>{username}</strong></p>

    {auth_warning}

    <h2>Generate New API Key</h2>
    <form method="POST" action="/admin/generate">
        <div class="form-group">
            <label>Username: <input type="text" name="username" required></label>
        </div>
        <div class="form-group">
            <label>Description: <input type="text" name="description" required></label>
        </div>
        <button type="submit">Generate Key</button>
    </form>

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

    <p><a href="/">← Back to Home</a></p>
</body>
</html>
        """
        )

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
            return HTMLResponse("Missing username", status_code=400)

        try:
            api_key = api_key_manager.create_key(key_username, description)
            logger.info(f"Generated API key for {key_username} by {username}")

            return HTMLResponse(
                f"""
<!DOCTYPE html>
<html>
<head><title>API Key Generated</title></head>
<body style="font-family: sans-serif; max-width: 800px; margin: 50px auto; padding: 0 20px;">
    <h1>API Key Generated</h1>
    <p><strong>Username:</strong> {key_username}</p>
    <p><strong>Description:</strong> {description}</p>
    <p><strong>API Key:</strong> <code style="background: #f5f5f5; padding: 5px;">{api_key}</code></p>
    <p style="color: red;"><strong>Important:</strong> Save this key now. It cannot be retrieved later.</p>
    <p><a href="/admin">Back to Admin</a></p>
</body>
</html>
            """
            )
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)

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
            return HTMLResponse("Missing api_key", status_code=400)

        try:
            success = api_key_manager.revoke_key(api_key)
            if success:
                logger.info(f"Revoked API key {api_key[:12]}... by {username}")
                return RedirectResponse(url="/admin", status_code=303)
            else:
                return HTMLResponse("API key not found", status_code=404)
        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)


