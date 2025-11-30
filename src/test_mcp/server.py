"""Minimal MCP server with OAuth and HTTPS using FastMCP."""

from dotenv import load_dotenv

# Load environment variables early
load_dotenv()

import logging
import os
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from .oauth import GitHubOAuthProvider
from . import html_templates
from . import audit
from . import admin

# Configure logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # Default for all libraries
MCP_LOG_LEVEL = os.getenv("MCP_LOG_LEVEL", LOG_LEVEL).upper()  # For our code
AUDIT_LOG_FILE = os.getenv("AUDIT_LOG_FILE", "")

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Set our own modules to MCP_LOG_LEVEL
logging.getLogger("test_mcp").setLevel(MCP_LOG_LEVEL)

# Setup audit logging to file if path is set
if AUDIT_LOG_FILE:
    from pathlib import Path
    audit_log_path = Path(AUDIT_LOG_FILE)
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    audit_logger = logging.getLogger("test_mcp.audit")
    file_handler = logging.FileHandler(audit_log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    audit_logger.addHandler(file_handler)
    audit_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Configuration
BASE_URL = os.getenv("BASE_URL", "https://127.0.0.1")
PORT = int(os.getenv("PORT", "8443"))
HOST = os.getenv("HOST", "127.0.0.1")
USE_HTTPS = os.getenv("USE_HTTPS", "true").lower() == "true"

# Create OAuth provider
oauth_provider = GitHubOAuthProvider()

# Create FastMCP with OAuth
mcp = FastMCP(
    "test-mcp",
    auth=AuthSettings(
        issuer_url=BASE_URL,
        resource_server_url=f"{BASE_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
    auth_server_provider=oauth_provider,
)


@mcp.tool()
def generate_html(title: str, content: str) -> str:
    """Generate simple HTML page.

    Args:
        title: Page title
        content: Page content
    """
    html = f"""<!DOCTYPE html>
<html>
<head><title>{title}</title></head>
<body>
    <h1>{title}</h1>
    <p>{content}</p>
</body>
</html>"""
    return html


@mcp.resource("status://live")
async def server_status() -> str:
    """Get live server status with current timestamp."""
    from datetime import datetime
    import json

    now = datetime.now()
    active_sessions = await oauth_provider.get_active_sessions_count()
    return json.dumps({
        "server": "test-mcp",
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_info": "Server is running",
        "active_sessions": active_sessions,
        "base_url": BASE_URL,
    }, indent=2)


@mcp.resource("log://{name}")
def read_log(name: str) -> str:
    """Read a log file by name.

    Args:
        name: Log file name
    """
    return f"This is the content of the {name} log file.\nExample log entry: [2025-01-29 12:00:00] INFO: Sample log message"


@mcp.prompt()
def webpage_prompt(topic: str) -> str:
    """Generate a prompt for creating a webpage about a topic.

    Args:
        topic: The topic for the webpage
    """
    return f"Create a simple HTML webpage about {topic} using the generate_html tool."


def main():
    """Run the server."""
    import uvicorn
    import json
    from starlette.responses import HTMLResponse, RedirectResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    app = mcp.streamable_http_app()

    # Note: SessionStore automatically loads data at initialization (for disk storage)
    # No need for explicit startup event - loading happens in SessionStore.__init__

    # Audit and debug middleware
    class AuditMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Log all incoming requests (DEBUG level only)
            if MCP_LOG_LEVEL == "DEBUG":
                scheme = request.url.scheme.upper()  # HTTP or HTTPS
                method = request.method
                path = request.url.path
                logger.debug(f"[{scheme}] {method} {path}")

            if request.url.path == "/mcp" and request.method == "POST":
                # Extract token and username for audit logging
                auth_header = request.headers.get("authorization", "")
                username = None
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    username = await oauth_provider.get_username_for_token(token)

                # Read and parse JSON-RPC request for tool calls
                if username and AUDIT_LOG_FILE:
                    body = await request.body()
                    try:
                        rpc_request = json.loads(body)

                        # Check if this is a tool call (tools/call method)
                        if isinstance(rpc_request, dict) and rpc_request.get("method") == "tools/call":
                            params = rpc_request.get("params", {})
                            tool_name = params.get("name", "unknown")
                            tool_args = params.get("arguments", {})

                            # Log the tool call
                            audit.log_tool_call(username, tool_name, tool_args)

                        # Reconstruct request with body
                        scope = request.scope
                        async def receive():
                            return {"type": "http.request", "body": body}
                        request = Request(scope, receive)
                    except Exception as e:
                        logger.error(f"Error parsing request for audit: {e}")

                # Debug logging
                if MCP_LOG_LEVEL == "DEBUG":
                    logger.debug(f"Request: {request.method} {request.url.path}")
                    logger.debug(f"Headers: {dict(request.headers)}")
                    if auth_header:
                        logger.debug(f"Authorization: {auth_header[:50]}...")
                    else:
                        logger.debug("No Authorization header found")

            response = await call_next(request)

            if request.url.path == "/mcp" and MCP_LOG_LEVEL == "DEBUG":
                logger.debug(f"Response status: {response.status_code}")

            return response

    app.add_middleware(AuditMiddleware)

    # Root endpoint - landing page
    @app.route("/")
    async def root(request):
        active_sessions = await oauth_provider.get_active_sessions_count()
        username = await web_session_manager.get_session_username(request)
        return HTMLResponse(html_templates.root_page(active_sessions, oauth_provider.required_repo, username))

    # GitHub OAuth callback - handles redirect from GitHub
    @app.route("/oauth/github/callback")
    async def github_callback(request):
        code = request.query_params.get("code")
        state = request.query_params.get("state")

        if not code or not state:
            return HTMLResponse("Missing code or state", status_code=400)

        try:
            # Handle GitHub callback (returns either string URL or RedirectResponse)
            result = await oauth_provider.handle_github_callback(code, state)
            # If it's already a Response object (web login), return it directly
            if hasattr(result, 'status_code'):
                return result
            # Otherwise it's a URL string (MCP OAuth), wrap in RedirectResponse
            return RedirectResponse(result)
        except Exception as e:
            return HTMLResponse(f"OAuth Error: {str(e)}", status_code=400)

    # Status endpoint
    @app.route("/status")
    async def status(request):
        active_sessions = await oauth_provider.get_active_sessions_count()
        return HTMLResponse(html_templates.status_page(active_sessions))

    # Setup shared web session manager for admin and web interfaces
    from .web_auth import WebSessionManager, setup_shared_auth_routes
    web_session_manager = WebSessionManager(oauth_provider)

    # Setup unified login/logout routes and get callback handler
    unified_callback_handler = setup_shared_auth_routes(app, web_session_manager)
    oauth_provider.set_web_callback_handler(unified_callback_handler)

    # Setup admin routes (OAuth protected web interface for API key management)
    admin.setup_admin_routes(app, oauth_provider, web_session_manager)

    # Setup web routes (OAuth protected web interface for interactive tools)
    from . import web
    web.setup_web_routes(app, oauth_provider, web_session_manager)

    if USE_HTTPS:
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            ssl_keyfile="certs/key.pem",
            ssl_certfile="certs/cert.pem",
            log_level="debug",
        )
    else:
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            log_level="debug",
        )


if __name__ == "__main__":
    main()
