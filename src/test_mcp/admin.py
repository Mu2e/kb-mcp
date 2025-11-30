"""Admin web interface for managing API keys (OAuth protected via GitHub)."""

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
        username = session_manager.get_session_username(request)
        if not username:
            # Redirect to login with return path
            return RedirectResponse(url="/login?redirect=/admin")

        # Check admin permissions
        if not session_manager.has_admin_access(request):
            return HTMLResponse(
                "Access denied: Admin permissions required",
                status_code=403
            )

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        # Get all API keys
        keys = api_key_manager.list_keys()
        keys_html = ""
        for key in keys:
            keys_html += f"""
            <tr>
                <td>{key['username']}</td>
                <td>{key['description']}</td>
                <td>{key.get('created_at', 'N/A')}</td>
                <td>
                    <form method="POST" action="/admin/revoke" style="display:inline">
                        <input type="hidden" name="username" value="{key['username']}">
                        <button type="submit" onclick="return confirm('Revoke key for {key['username']}?')">Revoke</button>
                    </form>
                </td>
            </tr>
            """

        return HTMLResponse(f"""
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
        """)

    @app.route("/admin/generate", methods=["POST"])
    async def admin_generate(request):
        """Generate API key."""
        username = session_manager.get_session_username(request)
        if not username:
            return RedirectResponse(url="/login?redirect=/admin")

        if not session_manager.has_admin_access(request):
            return HTMLResponse("Access denied: Admin permissions required", status_code=403)

        form = await request.form()
        key_username = form.get("username")
        description = form.get("description", "")

        if not key_username:
            return HTMLResponse("Missing username", status_code=400)

        try:
            api_key = api_key_manager.generate_key(key_username, description)
            logger.info(f"Generated API key for {key_username} by {username}")

            return HTMLResponse(f"""
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
            """)
        except Exception as e:
            logger.error(f"Error generating API key: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)

    @app.route("/admin/revoke", methods=["POST"])
    async def admin_revoke(request):
        """Revoke API key."""
        username = session_manager.get_session_username(request)
        if not username:
            return RedirectResponse(url="/login?redirect=/admin")

        if not session_manager.has_admin_access(request):
            return HTMLResponse("Access denied: Admin permissions required", status_code=403)

        form = await request.form()
        key_username = form.get("username")

        if not key_username:
            return HTMLResponse("Missing username", status_code=400)

        try:
            success = api_key_manager.revoke_key(key_username)
            if success:
                logger.info(f"Revoked API key for {key_username} by {username}")
                return RedirectResponse(url="/admin", status_code=303)
            else:
                return HTMLResponse(f"No API key found for {key_username}", status_code=404)
        except Exception as e:
            logger.error(f"Error revoking API key: {e}")
            return HTMLResponse(f"Error: {str(e)}", status_code=400)
