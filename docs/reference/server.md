# Server

The server module provides both MCP (Model Context Protocol) and web interfaces built on FastAPI with FastMCP.

**Architecture:**
- Built on [FastMCP](https://github.com/jlowin/fastmcp) which provides MCP server capabilities on top of FastAPI
- MCP tools defined in `mcp.py` - see [MCP Tools Reference](mcp.md)
- Web routes organized in `web/routes/` directory
- Admin API for managing MCP API keys in `admin.py`
- API routes in `api.py` for programmatic access

See [MCP documentation](../guides/mcp-setup.md) and [Web Interface documentation](../guides/web-interface.md) for setup and usage details.

## Server Entrypoint

::: kb_mcp.server.main
