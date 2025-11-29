"""Admin web interface for managing API keys (OAuth protected via GitHub)."""

import logging
import secrets
from starlette.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)


def setup_admin_routes(app, oauth_provider):
    """Setup admin web interface routes."""
    api_key_manager = oauth_provider.api_key_manager

    # Store browser sessions (in production, use Redis or similar)
    browser_sessions = {}

    async def get_session_username(request):
        """Get username from browser session cookie."""
        session_id = request.cookies.get("admin_session")
        if not session_id:
            return None
        return browser_sessions.get(session_id)

    @app.route("/admin")
    async def admin_page(request):
        """Admin interface (GitHub OAuth protected)."""
        username = await get_session_username(request)
        if not username:
            # Redirect to login
            return RedirectResponse(url="/admin/login")

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
    <h1>API Key Management <a href="/admin/logout" class="logout">Logout</a></h1>
    <p>Authenticated as: <strong>{username}</strong></p>

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

    @app.route("/admin/login")
    async def admin_login(request):
        """Initiate GitHub OAuth login."""
        state = secrets.token_urlsafe(32)
        # Prefix state with "admin_" to distinguish from MCP OAuth
        admin_state = f"admin_{state}"
        github_auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={oauth_provider.github_client_id}"
            f"&redirect_uri={oauth_provider.base_url}/oauth/github/callback"
            f"&state={admin_state}"
            f"&scope=read:user"
        )
        # Store state to verify callback (no prefix needed - just the raw state)
        browser_sessions[state] = True
        return RedirectResponse(url=github_auth_url)

    async def handle_admin_callback(code: str, state: str):
        """Handle admin OAuth callback (called from main OAuth callback)."""
        # Remove "admin_" prefix to get original state
        state = state.removeprefix("admin_")

        # Verify state (no prefix in storage)
        if not browser_sessions.pop(state, None):
            return HTMLResponse("Invalid state", status_code=400)

        # Use oauth_provider's helper methods to get GitHub token and verify user
        try:
            # Exchange code for GitHub access token
            github_token = await oauth_provider.exchange_github_code(code)

            # Get GitHub user info
            user_data = await oauth_provider.get_github_user(github_token)
            username = user_data.get('login')

            # Check repository admin access if required
            if oauth_provider.required_repo:
                has_admin = await oauth_provider.verify_repo_access(github_token, require_admin=True)
                if not has_admin:
                    return HTMLResponse(
                        f"Access denied: You need admin permissions on {oauth_provider.required_repo}",
                        status_code=403
                    )
        except Exception as e:
            logger.error(f"OAuth error: {e}")
            return HTMLResponse(f"OAuth error: {str(e)}", status_code=400)

        # Create browser session
        session_id = secrets.token_urlsafe(32)
        browser_sessions[session_id] = username
        logger.info(f"Admin login: {username}")

        # Set cookie and redirect
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=session_id,
            httponly=True,
            secure=True,
            samesite="lax"
        )
        return response

    @app.route("/admin/logout")
    async def admin_logout(request):
        """Logout from admin interface."""
        session_id = request.cookies.get("admin_session")
        if session_id:
            browser_sessions.pop(session_id, None)

        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie("admin_session")
        return response

    @app.route("/admin/generate", methods=["POST"])
    async def admin_generate(request):
        """Generate API key."""
        username = await get_session_username(request)
        if not username:
            return RedirectResponse(url="/admin/login")

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
        username = await get_session_username(request)
        if not username:
            return RedirectResponse(url="/admin/login")

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

    # Return the admin callback handler for use in the main OAuth callback
    return handle_admin_callback
