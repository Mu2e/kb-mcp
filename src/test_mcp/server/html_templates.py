"""HTML templates for server endpoints."""


def get_default_nav_items() -> list[tuple[str, str]]:
    """Get default navigation items for all pages."""
    return [
        ("/", "Home"),
        ("/web", "Knowledge Base Explorer"),
        ("/web/upload", "Upload"),
        ("/admin", "Admin"),
        ("/status", "Status"),
    ]


def base_template(title: str, content: str, nav_items: list[tuple[str, str]] | None = None, username: str | None = None) -> str:
    """Generate base HTML template with navigation and styling.
    
    Args:
        title: Page title
        content: Main page content (HTML)
        nav_items: List of (url, label) tuples for navigation. If None, uses default navigation.
        username: Current username (if logged in)
    """
    if nav_items is None:
        nav_items = get_default_nav_items()
    
    nav_html = ""
    if nav_items:
        nav_html = '<div class="nav">'
        for url, label in nav_items:
            nav_html += f'<a href="{url}">{label}</a>'
        if username:
            nav_html += f'<a href="/logout" class="nav-right">Logout ({username})</a>'
        else:
            nav_html += '<a href="/login" class="nav-right">Login</a>'
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


def root_page(active_sessions: int, required_repo: str, username: str | None = None) -> str:
    """Generate the root landing page."""
    auth_status = (
        f"Restricted to users with access to: <code>{required_repo}</code>"
        if required_repo
        else "Open to all authenticated GitHub users"
    )

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
    <h1>MCP Server: test-mcp</h1>
    <p class="success-box" style="display: inline-block; padding: 10px 20px;">
        <strong>Server Status:</strong> Running
    </p>

    {user_status}

    <div class="card">
        <h2>About This Server</h2>
        <p>This is a Model Context Protocol (MCP) server with GitHub OAuth authentication.</p>
        <p><strong>Authorization:</strong> {auth_status}</p>
        <p><strong>Active Sessions:</strong> {active_sessions}</p>
    </div>

    <div class="card">
        <h2>MCP Endpoint</h2>
        <div class="info-box">
            <strong>Endpoint:</strong> <a href="/mcp"><code>/mcp</code></a><br>
            <strong>Protocol:</strong> MCP over HTTP (streamable)<br>
            <strong>Authentication:</strong> OAuth 2.0 with GitHub
        </div>
        <p>MCP clients (like Claude Desktop) connect to <code>/mcp</code> and handle OAuth automatically.</p>
    </div>

    <div class="card">
        <h2>Available Tools</h2>
        <ul>
            <li><code>generate_html</code> - Generate simple HTML pages</li>
            <li><code>kb_get_document</code> - Get documents from the knowledge base</li>
        </ul>
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
            <li><code>/oauth/github/callback</code> - GitHub OAuth callback (used during auth flow)</li>
        </ul>
    </div>

    <p style="color: #666; margin-top: 40px;">
        For more information about MCP, visit <a href="https://modelcontextprotocol.io">modelcontextprotocol.io</a>
    </p>
    """
    
    return base_template("MCP Server - test-mcp", content, None, username)


def status_page(active_sessions: int) -> str:
    """Generate the simple status page."""
    content = f"""
    <h1>MCP Server Status: OK</h1>
    <div class="success-box">
        <p><strong>Server:</strong> test-mcp v0.1.0</p>
        <p><strong>Endpoint:</strong> <a href="/mcp">/mcp</a> (OAuth protected with GitHub)</p>
        <p><strong>Active sessions:</strong> {active_sessions}</p>
        <p>OAuth is handled automatically by MCP clients</p>
    </div>
    """
    
    return base_template("MCP Server Status", content, None)
