"""Protected web interface for interactive MCP tool usage (server package)."""

import logging
from starlette.responses import HTMLResponse, RedirectResponse

from .web_auth import WebSessionManager

logger = logging.getLogger(__name__)


def setup_web_routes(app, oauth_provider, session_manager: WebSessionManager):
    """Setup web interface routes."""

    @app.route("/web")
    async def web_page(request):
        """Web interface (GitHub OAuth protected)."""
        username = await session_manager.get_session_username(request)
        if not username:
            # Redirect to login with return path
            return RedirectResponse(url="/login?redirect=/web")

        # Get auth warning banner
        auth_warning = session_manager.get_auth_warning_html()

        return HTMLResponse(
            f"""
<!DOCTYPE html>
<html>
<head>
    <title>MCP Web Interface</title>
    <style>
        body {{ font-family: sans-serif; max-width: 1200px; margin: 50px auto; padding: 0 20px; }}
        h1 {{ color: #333; }}
        .logout {{ float: right; }}
        .placeholder {{ background: #f5f5f5; padding: 40px; text-align: center; color: #666; border-radius: 8px; margin: 40px 0; }}
    </style>
</head>
<body>
    <h1>MCP Web Interface <a href="/logout" class="logout">Logout</a></h1>
    <p>Authenticated as: <strong>{username}</strong></p>

    {auth_warning}

    <div class="placeholder">
        <h2>Coming Soon</h2>
        <p>Interactive web interface for MCP tools will be available here.</p>
        <p>This is a protected area - only users with repository access can see this page.</p>
    </div>

    <p><a href="/">← Back to Home</a></p>
</body>
</html>
        """
        )


