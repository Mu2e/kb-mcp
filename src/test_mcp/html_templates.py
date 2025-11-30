"""HTML templates for server endpoints."""


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
        <div style="background: #dcfce7; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #16a34a;">
            <p style="margin: 0;">
                <strong>Logged in as:</strong> {username}
                <a href="/logout" style="margin-left: 20px;">Logout</a>
            </p>
        </div>
        """
    else:
        user_status = """
        <div style="background: #fef3c7; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
            <p style="margin: 0;">
                <strong>Not logged in</strong>
                <a href="/login" style="margin-left: 20px;">Login</a>
            </p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>MCP Server - test-mcp</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
        h1 {{ color: #2563eb; }}
        .info {{ background: #f0f9ff; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .endpoint {{ background: #f9fafb; padding: 15px; border-left: 4px solid #2563eb; margin: 10px 0; }}
        code {{ background: #e5e7eb; padding: 2px 6px; border-radius: 4px; }}
        .status {{ color: #16a34a; font-weight: bold; }}
        a {{ color: #2563eb; }}
    </style>
</head>
<body>
    <h1>MCP Server: test-mcp</h1>
    <p class="status">Server Status: Running</p>

    {user_status}

    <div class="info">
        <h2>About This Server</h2>
        <p>This is a Model Context Protocol (MCP) server with GitHub OAuth authentication.</p>
        <p><strong>Authorization:</strong> {auth_status}</p>
        <p><strong>Active Sessions:</strong> {active_sessions}</p>
    </div>

    <div class="info">
        <h2>MCP Endpoint</h2>
        <div class="endpoint">
            <strong>Endpoint:</strong> <a href="/mcp"><code>/mcp</code></a><br>
            <strong>Protocol:</strong> MCP over HTTP (streamable)<br>
            <strong>Authentication:</strong> OAuth 2.0 with GitHub
        </div>
        <p>MCP clients (like Claude Desktop) connect to <code>/mcp</code> and handle OAuth automatically.</p>
    </div>

    <div class="info">
        <h2>Available Tools</h2>
        <ul>
            <li><code>generate_html</code> - Generate simple HTML pages</li>
        </ul>
    </div>

    <div class="info">
        <h2>Web Interfaces</h2>
        <ul>
            <li><a href="/admin"><code>/admin</code></a> - API Key Management (requires admin permissions)</li>
            <li><a href="/web"><code>/web</code></a> - Interactive Web Interface (requires repository access)</li>
        </ul>
    </div>

    <div class="info">
        <h2>Other Endpoints</h2>
        <ul>
            <li><a href="/status"><code>/status</code></a> - Server status (simpler version)</li>
            <li><code>/oauth/github/callback</code> - GitHub OAuth callback (used during auth flow)</li>
        </ul>
    </div>

    <p style="color: #6b7280; margin-top: 40px;">
        For more information about MCP, visit <a href="https://modelcontextprotocol.io">modelcontextprotocol.io</a>
    </p>
</body>
</html>"""


def status_page(active_sessions: int) -> str:
    """Generate the simple status page."""
    return f"""<!DOCTYPE html>
<html>
<head><title>MCP Server Status</title></head>
<body>
    <h1>MCP Server Status: OK</h1>
    <p>Server: test-mcp v0.1.0</p>
    <p>Endpoint: <a href="/mcp">/mcp</a> (OAuth protected with GitHub)</p>
    <p>Active sessions: {active_sessions}</p>
    <p>OAuth is handled automatically by MCP clients</p>
</body>
</html>"""
