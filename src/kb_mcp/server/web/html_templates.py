"""HTML templates for server endpoints."""

from ...config import get_server_config


def get_site_name() -> str:
    """Get the configured display name for the web UI."""
    return get_server_config()['site_name']


def get_default_nav_items(is_admin: bool = True) -> list[tuple[str, str]]:
    """Navigation items for all pages.

    Args:
        is_admin: When False, only the publicly browsable pages are listed.
            Statistics, logs, evaluations, upload and admin are omitted so
            that a logged-out visitor is not shown links they cannot follow.
    """
    server_config = get_server_config()
    items = [
        ("/web", get_site_name()),
        ("/web/chat", "Chat"),
    ]
    if not server_config['hide_graph']:
        items.append(("/web/graph", "Knowledge Graph"))

    if is_admin:
        items += [
            ("/web/eval", "Evaluations"),
            #("/web/compare", "Parser Compare"),
            ("/web/statistics", "Statistics"),
            ("/web/logs", "Logs"),
            ("/web/upload", "Upload"),
            ("/admin", "Admin"),
        ]
    items.append(("/status", "Status"))
    return items


def base_template(
    title: str,
    content: str,
    nav_items: list[tuple[str, str]] | None = None,
    username: str | None = None,
    *,
    is_admin: bool | None = None,
) -> str:
    """Generate base HTML template with navigation and styling.

    Args:
        title: Page title
        content: Main page content (HTML)
        nav_items: List of (url, label) tuples for navigation. If None, uses
            navigation appropriate to `is_admin`.
        username: Current username (if logged in). Retained for callers that
            still pass it; the nav shows admin state rather than identity.
        is_admin: Whether the visitor has admin access. When None it is
            resolved from the request-independent config: if no admin password
            is configured there is nothing to log in to, so the full menu is
            shown (matching the behaviour before the public/admin split).
    """
    if is_admin is None:
        from ...config import get_auth_config

        auth_config = get_auth_config()
        gate_configured = bool(
            auth_config['admin_password'] or auth_config['admin_password_hash']
        )
        if not gate_configured:
            # Nothing to log in to. is_admin_unlocked() likewise returns True
            # in this case, so the admin pages are reachable and the nav must
            # list them - otherwise they would be unreachable rather than
            # merely unprotected.
            is_admin = True
        else:
            # Callers pass the session's username; a logged-in visitor is an
            # admin, since in public mode a session only exists after clearing
            # /admin/login (or an OAuth login, which sets has_admin itself).
            is_admin = bool(username)

    if nav_items is None:
        nav_items = get_default_nav_items(is_admin=is_admin)
    
    nav_html = ""
    if nav_items:
        nav_html = '<div class="nav">'
        for url, label in nav_items:
            nav_html += f'<a href="{url}">{label}</a>'
        # "Logged in" here means admin: the public pages need no identity at
        # all, so the only state worth reflecting in the nav is whether the
        # visitor has cleared the admin gate.
        if is_admin:
            nav_html += '<a href="/admin/logout" class="nav-right">Logout</a>'
        else:
            nav_html += '<a href="/admin/login" class="nav-right">Login</a>'
        nav_html += '</div>'
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
    <div class="container">
        {nav_html}
        {content}
    </div>
    <script src="/static/js/app.js"></script>
</body>
</html>"""


def root_page(active_sessions: int, required_access: str | None = None, username: str | None = None, provider_display: str | None = None) -> str:
    """Generate the root landing page."""
    # Authentication provider info
    if provider_display:
        provider_info = f"<strong>Authentication Provider:</strong> {provider_display}"
    else:
        provider_info = "<strong>Authentication Provider:</strong> Not configured"
    
    # Access restriction info
    if required_access:
        auth_status = f"Restricted to users with access to: <code>{required_access}</code>"
    else:
        auth_status = "Open to all authenticated users"

    # User session status
    if username:
        user_status = f"""
        <div class="success-box">
            <p style="margin: 0;">
                <strong>Logged in as:</strong> {username}
            </p>
        </div>
        """
    else:
        user_status = """
        <div class="warning-box">
            <p style="margin: 0;">
                <strong>Not logged in</strong>
            </p>
        </div>
        """

    content = f"""
    <h1>MCP Server: {get_site_name()}</h1>
    <p class="success-box" style="display: inline-block; padding: 10px 20px;">
        <strong>Server Status:</strong> Running
    </p>

    {user_status}

    <div class="card">
        <h2>About This Server</h2>
        <p>This is a Model Context Protocol (MCP) server with OAuth authentication.</p>
        <p>{provider_info}</p>
        <p><strong>Authorization:</strong> {auth_status}</p>
        <p><strong>Active Sessions:</strong> {active_sessions}</p>
    </div>

    <div class="card">
        <h2>MCP Endpoint</h2>
        <div class="info-box">
            <strong>Endpoint:</strong> <a href="/mcp"><code>/mcp</code></a><br>
            <strong>Protocol:</strong> MCP over HTTP (streamable)<br>
            <strong>Authentication:</strong> OAuth 2.0 or API Keys
        </div>
        <p>MCP clients (like Claude Desktop) connect to <code>/mcp</code> and handle OAuth automatically.</p>
    </div>

    <div class="card">
        <h2>Available MCP Tools</h2>
        <ul>
            <li><code>kb_search</code> - Search the knowledge base using semantic search</li>
            <li><code>kb_get</code> - Get a specific document by identifier</li>
        </ul>
        <p><a href="https://github.com/HEP-KE/kb-mcp/blob/sld/docs/reference/mcp.md">View full tool documentation →</a></p>
    </div>

    <div class="card">
        <h2>Web Interfaces</h2>
        <ul>
            <li><a href="/admin"><code>/admin</code></a> - API Key Management (requires admin permissions)</li>
            <li><a href="/web"><code>/web</code></a> - Interactive Web Interface (requires repository access)</li>
        </ul>
    </div>

    <div class="card">
        <h2>Other Endpoints</h2>
        <ul>
            <li><a href="/status"><code>/status</code></a> - Server status (simpler version)</li>
            <li><code>/oauth/callback</code> - OAuth callback (used during auth flow)</li>
        </ul>
    </div>

    <p style="color: #666; margin-top: 40px;">
        <a href="https://github.com/HEP-KE/kb-mcp">GitHub Repository</a> · 
        <a href="https://github.com/HEP-KE/kb-mcp/blob/sld/docs/index.md">Documentation</a> · 
        <a href="https://modelcontextprotocol.io">Model Context Protocol</a>
    </p>
    """
    
    return base_template("MCP Server - kb-mcp", content, None, username)


def status_page(active_sessions: int) -> str:
    """Generate the simple status page."""
    content = f"""
    <h1>MCP Server Status: OK</h1>
    <div class="success-box">
        <p><strong>Server:</strong> kb-mcp v0.1.0</p>
        <p><strong>Endpoint:</strong> <a href="/mcp">/mcp</a> (OAuth protected with GitHub)</p>
        <p><strong>Active sessions:</strong> {active_sessions}</p>
        <p>OAuth is handled automatically by MCP clients</p>
    </div>
    """
    
    return base_template("MCP Server Status", content, None)

