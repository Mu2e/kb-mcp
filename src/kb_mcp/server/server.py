"""MCP server application with OAuth and HTTPS using FastMCP."""

from dotenv import load_dotenv
from pathlib import Path

# Load environment variables early
# Find project root (where .env file is located)
# Go up from src/kb_mcp/server/server.py to project root
project_root = Path(__file__).parent.parent.parent.parent
env_path = project_root / ".env"
load_dotenv(env_path)

import contextlib
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
# MCP and the web UI bind separately; see the app split further down.
MCP_HOST = _server_config['mcp_host']
WEB_HOST = _server_config['web_host']
WEB_PORT = _server_config['web_port']
USE_HTTPS = _server_config['use_https']

auth_config = get_auth_config()

# The MCP endpoint and the web UI are served on separate sockets (see main()),
# so they gate access independently. MCP_REQUIRE_API_KEY / WEB_REQUIRE_AUTH
# override DISABLE_AUTH per surface; see config.get_auth_config.
MCP_REQUIRE_API_KEY = auth_config['mcp_require_api_key']
WEB_REQUIRE_AUTH = auth_config['web_require_auth']

if MCP_REQUIRE_API_KEY:
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



from .mcp_prompts import get_server_instructions

# Create FastMCP with OAuth (only if provider is configured)
if MCP_REQUIRE_API_KEY:
    mcp = FastMCP(
        "kb-mcp",
        instructions=get_server_instructions(),
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
        instructions=get_server_instructions(),
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

# Root responder redirects to the Knowledge Base Explorer (the web UI landing page)
async def root_responder(request):
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/web?doc_type=text")

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

# The MCP endpoint and the web UI are two separate Starlette applications,
# served by two uvicorn servers on two sockets (see main()). They are split so
# they can have genuinely different exposure: MCP listens on MCP_HOST (often a
# network interface) and is gated by an API key or OAuth token, while the web
# UI listens on WEB_HOST, which defaults to loopback. Binding is what keeps the
# web UI off the network - not a check inside the application.
#
# There is no HTTP traffic between them: the web chat page talks to MCP by
# spawning `python -m kb_mcp.server.mcp_stdio` over stdio, not over the wire.
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

static_path = Path(__file__).parent / "static"

# --- MCP application -------------------------------------------------------
# `app` remains the MCP application so existing ASGI entry points
# (e.g. `uvicorn kb_mcp.server.server:app`) keep working.
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

# Add audit middleware
app.add_middleware(AuditMiddleware)

# The OAuth callback lives on the MCP app because the MCP authorization flow
# redirects to it. The web login flow uses the copy on the web app.
app.add_route("/oauth/callback", oauth_callback_responder)


def create_web_app() -> Starlette:
    """Build the web UI application.

    Kept separate from the MCP app so the two can be bound to different
    addresses. Every route module already takes the app as an argument, so
    nothing below is web-server specific beyond the wiring.
    """
    web_app = Starlette()

    # Static assets (CSS, JS) for the pages.
    web_app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    # Login/logout, and the OAuth callback for the *web* login flow.
    setup_shared_auth_routes(web_app, web_session_manager)
    web_app.add_route("/oauth/callback", oauth_callback_responder)

    web_app.add_route("/", root_responder)
    web_app.add_route("/status", status_responder)

    # Documents, eval, logs, statistics, admin, API, graph and chat routes.
    setup_web_routes(web_app, oauth_provider, web_session_manager)

    # setup_chat_routes stashes a cleanup coroutine on app.state; start it with
    # the web app's lifespan (the MCP app's lifespan belongs to FastMCP's
    # session manager and must not be replaced).
    @contextlib.asynccontextmanager
    async def _web_lifespan(app_):
        import asyncio

        if hasattr(app_.state, "chat_cleanup_task"):
            asyncio.create_task(app_.state.chat_cleanup_task())
            logger.info("Started chat session cleanup background task")
        yield

    web_app.router.lifespan_context = _web_lifespan
    return web_app


def main():
    """Run the MCP server, the web UI server, or both."""
    import argparse
    import asyncio
    import uvicorn

    parser = argparse.ArgumentParser(
        prog="kb-server",
        description=(
            "Run the knowledge base servers. By default both are started: the "
            "MCP endpoint on MCP_HOST:PORT and the web UI on WEB_HOST:WEB_PORT "
            "(loopback by default)."
        ),
    )
    surface = parser.add_mutually_exclusive_group()
    surface.add_argument(
        "--only-mcp",
        action="store_true",
        help="Run only the MCP endpoint (no web UI).",
    )
    surface.add_argument(
        "--only-web",
        action="store_true",
        help="Run only the web UI (no MCP endpoint).",
    )
    parser.add_argument("--host", help="Override the MCP bind address (MCP_HOST).")
    parser.add_argument("--port", type=int, help="Override the MCP port (PORT).")
    parser.add_argument("--web-host", help="Override the web UI bind address (WEB_HOST).")
    parser.add_argument("--web-port", type=int, help="Override the web UI port (WEB_PORT).")
    args = parser.parse_args()

    run_mcp = not args.only_web
    run_web = not args.only_mcp

    mcp_host = args.host or MCP_HOST
    mcp_port = args.port or PORT
    web_host = args.web_host or WEB_HOST
    web_port = args.web_port or WEB_PORT

    if run_mcp and not MCP_REQUIRE_API_KEY:
        logger.warning(
            "MCP endpoint is running WITHOUT authentication on %s:%s. "
            "Set MCP_REQUIRE_API_KEY=true (or remove DISABLE_AUTH) to require "
            "an API key.",
            mcp_host,
            mcp_port,
        )
    if run_web and not WEB_REQUIRE_AUTH and web_host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Web UI has authentication disabled but is bound to %s, which is not "
            "loopback. Anyone who can reach that address has full access. "
            "Set WEB_REQUIRE_AUTH=true or bind WEB_HOST to 127.0.0.1.",
            web_host,
        )
    if not MCP_REQUIRE_API_KEY and USE_HTTPS:
        logger.warning("MCP authentication is disabled but HTTPS is enabled. Is this intended?")

    def _ssl_kwargs():
        if not USE_HTTPS:
            return {}
        return {"ssl_keyfile": "certs/key.pem", "ssl_certfile": "certs/cert.pem"}

    async def _serve():
        servers = []

        if run_mcp:
            logger.info("MCP endpoint listening on %s:%s", mcp_host, mcp_port)
            servers.append(
                uvicorn.Server(
                    uvicorn.Config(app, host=mcp_host, port=mcp_port, **_ssl_kwargs())
                ).serve()
            )

        if run_web:
            logger.info("Web UI listening on %s:%s", web_host, web_port)
            servers.append(
                uvicorn.Server(
                    uvicorn.Config(
                        create_web_app(), host=web_host, port=web_port, **_ssl_kwargs()
                    )
                ).serve()
            )

        await asyncio.gather(*servers)

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
