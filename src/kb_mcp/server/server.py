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

from .oauth import GitHubOAuthProvider
from . import html_templates
from . import audit
from . import admin
from . import mcp as mcp_tools
from ..config import get_server_config

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

# Create OAuth provider
oauth_provider = GitHubOAuthProvider()

# Create FastMCP with OAuth
mcp = FastMCP(
    "kb-mcp",
    auth=AuthSettings(
        issuer_url=BASE_URL,
        resource_server_url=f"{BASE_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
    ),
    auth_server_provider=oauth_provider,
)


# Register MCP tools, resources, and prompts
mcp_tools.register_tools(mcp)
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

    # Root endpoint - landing page
    @app.route("/")
    async def root(request):
        active_sessions = await oauth_provider.get_active_sessions_count()
        username = await web_session_manager.get_session_username(request)
        return HTMLResponse(
            html_templates.root_page(
                active_sessions, oauth_provider.required_repo, username
            )
        )

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
            if hasattr(result, "status_code"):
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
    from .web import WebSessionManager, setup_shared_auth_routes

    web_session_manager = WebSessionManager(oauth_provider)

    # Setup unified login/logout routes and get callback handler
    unified_callback_handler = setup_shared_auth_routes(app, web_session_manager)
    oauth_provider.set_web_callback_handler(unified_callback_handler)

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


