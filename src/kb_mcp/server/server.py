"""MCP server application with OAuth and HTTPS using FastMCP."""

from dotenv import load_dotenv
from pathlib import Path

# Load environment variables early
# Find project root (where .env file is located)
# Go up from src/kb_mcp/server/server.py to project root
project_root = Path(__file__).parent.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(env_path)

import logging
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

from .oauth_github import GitHubOAuthProvider
from .oauth_globus import GlobusOAuthProvider
from . import html_templates
from . import audit
from . import admin
from . import mcp as mcp_tools
from ..config import get_server_config, get_github_oauth_config, get_globus_oauth_config

# Configure logging
_server_config = get_server_config()
LOG_LEVEL = _server_config['log_level']
MCP_LOG_LEVEL = _server_config['mcp_log_level']
AUDIT_LOG_FILE = _server_config['audit_log_file']

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Set our own modules to MCP_LOG_LEVEL
logging.getLogger("kb_mcp").setLevel(MCP_LOG_LEVEL)

# Setup audit logging to file if path is set
if AUDIT_LOG_FILE:
    from pathlib import Path

    audit_log_path = Path(AUDIT_LOG_FILE)
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    audit_logger = logging.getLogger("kb_mcp.audit")
    file_handler = logging.FileHandler(audit_log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    audit_logger.addHandler(file_handler)
    audit_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# Configuration
from ..config import get_server_config

_server_config = get_server_config()
BASE_URL = _server_config['base_url']
PORT = _server_config['port']
HOST = _server_config['host']
USE_HTTPS = _server_config['use_https']

# Determine which OAuth provider to use (only one allowed for both MCP and web)
github_config = get_github_oauth_config()
globus_config = get_globus_oauth_config()
github_enabled = bool(github_config['client_id'] and github_config['client_secret'])
globus_enabled = bool(globus_config['client_id'] and globus_config['client_secret'])

if github_enabled and globus_enabled:
    raise ValueError(
        "Both GitHub and Globus OAuth are configured. "
        "Only one OAuth provider can be enabled at a time. "
        "Please set only GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET OR GLOBUS_CLIENT_ID/GLOBUS_CLIENT_SECRET."
    )

if github_enabled:
    # Create OAuth provider (used for both MCP and web)
    oauth_provider = GitHubOAuthProvider()
    logger.info("GitHub OAuth provider enabled for MCP and web authentication")
elif globus_enabled:
    # Create OAuth provider (used for both MCP and web)
    oauth_provider = GlobusOAuthProvider()
    logger.info("Globus OAuth provider enabled for MCP and web authentication")
else:
    oauth_provider = None
    logger.warning("No OAuth provider configured")

# Create FastMCP with OAuth (only if provider is configured)
if oauth_provider:
    mcp = FastMCP(
        "kb-mcp",
        auth=AuthSettings(
            issuer_url=BASE_URL,
            resource_server_url=f"{BASE_URL}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
        ),
        auth_server_provider=oauth_provider,
        # settings={"enable_dns_rebinding_protection": False} # add one new mcp version that supports this is avaialble, so far use <1.23.0
    )
else:
    # Create FastMCP without OAuth if no provider configured
    mcp = FastMCP("kb-mcp")


# Register MCP tools, resources, and prompts
mcp_tools.register_tools(mcp)
if oauth_provider:
    mcp_tools.register_resources(mcp, oauth_provider, BASE_URL)
mcp_tools.register_prompts(mcp)


def main():
    """Run the server."""
    import uvicorn
    import json
    from starlette.responses import HTMLResponse, RedirectResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.requests import Request
    from starlette.staticfiles import StaticFiles

    app = mcp.streamable_http_app()
    
    # Add CORS middleware for browser-based MCP clients (tested with MCP Inspector)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],  # Important for MCP session tracking
    )
    
    # Serve static files (CSS, JS)
    static_path = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Audit and debug middleware
    class AuditMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Debug logging for all requests
            if MCP_LOG_LEVEL == "DEBUG" and request.url.path.startswith("/mcp"):
                logger.debug(f"MCP: {request.method} {request.url.path}")

            if request.url.path == "/mcp" and request.method == "POST":
                # Extract token and username for audit logging
                auth_header = request.headers.get("authorization", "")
                username = None
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    username = await oauth_provider.get_username_for_token(token)

                # Read and parse JSON-RPC request for tool call logging
                body = await request.body()
                try:
                    rpc_request = json.loads(body)
                    rpc_method = rpc_request.get("method", "unknown")
                    
                    # Log tool calls at INFO level
                    if rpc_method == "tools/call":
                        params = rpc_request.get("params", {})
                        tool_name = params.get("name", "unknown")
                        tool_args = params.get("arguments", {})
                        logger.info(f"MCP tool call: {tool_name}")
                        
                        # Audit logging to file
                        if username and AUDIT_LOG_FILE:
                            audit.log_tool_call(username, tool_name, tool_args)
                    elif MCP_LOG_LEVEL == "DEBUG":
                        logger.debug(f"MCP method: {rpc_method}")
                            
                except json.JSONDecodeError as e:
                    logger.warning(f"MCP: Failed to parse JSON: {e}")

                # Reconstruct request with body (since we consumed it)
                scope = request.scope
                async def receive():
                    return {"type": "http.request", "body": body}
                request = Request(scope, receive)

            response = await call_next(request)
            return response

    app.add_middleware(AuditMiddleware)

    # Setup shared web session manager for admin and web interfaces
    from .web import WebSessionManager, setup_shared_auth_routes

    web_session_manager = WebSessionManager(oauth_provider)

    # Setup unified login/logout routes
    setup_shared_auth_routes(app, web_session_manager)

    # Root endpoint - landing page
    @app.route("/")
    async def root(request):
        active_sessions = await oauth_provider.get_active_sessions_count() if oauth_provider else 0
        username = await web_session_manager.get_session_username(request)
        # Get required repo/group for display
        required_access = None
        if isinstance(oauth_provider, GitHubOAuthProvider):
            required_access = oauth_provider.required_repo
        elif isinstance(oauth_provider, GlobusOAuthProvider):
            required_access = oauth_provider.required_group
        return HTMLResponse(
            html_templates.root_page(
                active_sessions, required_access, username
            )
        )

    # Unified OAuth callback - handles both MCP and web OAuth flows
    @app.route("/oauth/callback")
    async def oauth_callback(request):
        code = request.query_params.get("code")
        state = request.query_params.get("state")

        if not code or not state:
            return HTMLResponse("Missing code or state", status_code=400)

        if not oauth_provider:
            return HTMLResponse("No OAuth provider configured", status_code=500)

        try:
            # Check if this is MCP OAuth flow (state in pending_auth)
            if state in oauth_provider.pending_auth:
                # MCP OAuth flow - handle via oauth_provider
                result = await oauth_provider.handle_callback(code, state)
                return RedirectResponse(result)
            else:
                # Web OAuth flow - handle via configured oauth_provider
                if not oauth_provider:
                    return HTMLResponse("No OAuth provider configured for web authentication", status_code=500)
                return await web_session_manager.handle_oauth_callback(oauth_provider, code, state)
        except Exception as e:
            return HTMLResponse(f"OAuth Error: {str(e)}", status_code=400)

    # Status endpoint
    @app.route("/status")
    async def status(request):
        active_sessions = await oauth_provider.get_active_sessions_count() if oauth_provider else 0
        return HTMLResponse(html_templates.status_page(active_sessions))

    # Setup admin routes (OAuth protected web interface for API key management)
    admin.setup_admin_routes(app, oauth_provider, web_session_manager)

    # Setup web routes (OAuth protected web interface for interactive tools)
    from .web import setup_web_routes
    setup_web_routes(app, oauth_provider, web_session_manager)

    # Setup API routes (OAuth protected API endpoints)
    from . import api
    api.setup_api_routes(app, web_session_manager)

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


