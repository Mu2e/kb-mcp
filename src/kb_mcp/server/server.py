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

from .oauth import GitHubOAuthProvider, GlobusOAuthProvider, BaseOAuthProvider
from .web import html_templates
from . import audit
from . import mcp as mcp_tools
from ..config import get_server_config, get_github_oauth_config, get_globus_oauth_config, get_auth_config

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
if PORT == 8888:
    raise ValueError(
        "Port 8888 is reserved and must not be used by this server. "
        "Port 8888 is commonly used by Jupyter notebooks, which would cause conflicts. "
        "Please set a different port via the PORT environment variable."
    )
HOST = _server_config['host']
USE_HTTPS = _server_config['use_https']

auth_config = get_auth_config()

if auth_config['disable_auth'] != True:
    if auth_config['oauth_provider'] is None:
         # No OAuth but auth required - use base class in API-key-only mode
        oauth_provider = BaseOAuthProvider(None, None)
        logger.info("API-key-only authentication enabled (no OAuth provider configured)")
    elif auth_config['oauth_provider'] == 'github':
        oauth_provider = GitHubOAuthProvider()
        logger.info("GitHub OAuth provider enabled for MCP and web authentication")
    elif auth_config['oauth_provider'] == 'globus':
        oauth_provider = GlobusOAuthProvider()
        logger.info("Globus OAuth provider enabled for MCP and web authentication")
else:
    # Auth disabled - no provider (will create FastMCP without auth)
    oauth_provider = None
    # logger.warning("Authentication disabled (DISABLE_AUTH=true)") # warning will be shown from web/auth.py



# Create FastMCP with OAuth (only if provider is configured)
if auth_config['disable_auth'] != True:
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
    # Create FastMCP without OAuth if authentication is disabled
    mcp = FastMCP("kb-mcp",
        auth=None, # no auth needed if authentication is disabled
    )


# Register MCP tools, resources, and prompts
mcp_tools.register_tools(mcp)
if oauth_provider:
    mcp_tools.register_resources(mcp, oauth_provider, BASE_URL)
mcp_tools.register_prompts(mcp)


# Setup shared web session manager for admin and web interfaces
from .web import WebSessionManager, setup_shared_auth_routes, setup_web_routes

web_session_manager = WebSessionManager(oauth_provider)

# Root/Status responders need access to oauth_provider and web_session_manager
async def root_responder(request):
    from .web import html_templates
    active_sessions = await oauth_provider.get_active_sessions_count() if oauth_provider else 0
    username = await web_session_manager.get_session_username(request)
    # Get required repo/group and provider info for display
    required_access = None
    provider_display = None
    if oauth_provider:
        if isinstance(oauth_provider, GitHubOAuthProvider):
            required_access = oauth_provider.required_repo
            provider_display = "GitHub"
        elif isinstance(oauth_provider, GlobusOAuthProvider):
            required_access = oauth_provider.required_group
            provider_display = "Globus"
        else:
            # API-key-only mode
            provider_display = "API Key Only"
    else:
        provider_display = "Disabled"
    from starlette.responses import HTMLResponse
    return HTMLResponse(
        html_templates.root_page(
            active_sessions, required_access, username, provider_display
        )
    )

async def status_responder(request):
    from .web import html_templates
    active_sessions = await oauth_provider.get_active_sessions_count() if oauth_provider else 0
    from starlette.responses import HTMLResponse
    return HTMLResponse(html_templates.status_page(active_sessions))

async def oauth_callback_responder(request):
    """Unified OAuth callback - handles both MCP and web OAuth flows."""
    from starlette.responses import HTMLResponse, RedirectResponse
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    try:
        if not code or not state:
            return HTMLResponse("Missing code or state", status_code=400)

        if not oauth_provider:
            return HTMLResponse("No OAuth provider configured", status_code=500)

        # Check if this is MCP OAuth flow (state in session store under pending_auth)
        pending_data = await oauth_provider.session_store.get("pending_auth", state)
        if pending_data:
            # MCP OAuth flow - handle via oauth_provider
            result = await oauth_provider.handle_callback(code, state)
            return RedirectResponse(result)
        else:
            # Web OAuth flow - handle via configured oauth_provider
            return await web_session_manager.handle_oauth_callback(oauth_provider, code, state)
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return HTMLResponse(f"OAuth Error: {str(e)}", status_code=400)

# Audit and debug middleware
from starlette.middleware.base import BaseHTTPMiddleware
class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import json
        from starlette.requests import Request
        # Debug logging for all requests
        if MCP_LOG_LEVEL == "DEBUG" and request.url.path.startswith("/mcp"):
            logger.debug(f"MCP: {request.method} {request.url.path}")

        if request.url.path == "/mcp" and request.method == "POST":
            # Extract token and username for audit logging
            auth_header = request.headers.get("authorization", "")
            username = None
            if auth_header.startswith("Bearer ") and oauth_provider:
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

        return await call_next(request)

# Create and configure the web application
from starlette.middleware.cors import CORSMiddleware
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

# Add audit middleware
app.add_middleware(AuditMiddleware)

# Setup unified login/logout routes
setup_shared_auth_routes(app, web_session_manager)

# Register routes
app.add_route("/oauth/callback", oauth_callback_responder)
app.add_route("/", root_responder)
app.add_route("/status", status_responder)

# Setup web routes (OAuth protected web interface for interactive tools)
# This now includes admin and API routes
setup_web_routes(app, oauth_provider, web_session_manager)


def main():
    """Run the server."""
    import asyncio
    import uvicorn

    if auth_config['disable_auth'] == True and USE_HTTPS:
        logger.warning("Authentication is disabled but HTTPS is enabled. Is this intended?")
        logger.warning("HTTPS is only needed if authentication is enabled.")
        logger.warning("To disable HTTPS, set USE_HTTPS=false in .env")

    async def _serve():
        # Start chat cleanup background task if registered
        if hasattr(app.state, 'chat_cleanup_task'):
            asyncio.create_task(app.state.chat_cleanup_task())
            logger.info("Started chat session cleanup background task")

        kwargs = dict(host=HOST, port=PORT, log_level="debug")
        if USE_HTTPS:
            kwargs["ssl_keyfile"] = "certs/key.pem"
            kwargs["ssl_certfile"] = "certs/cert.pem"

        config = uvicorn.Config(app, **kwargs)
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
