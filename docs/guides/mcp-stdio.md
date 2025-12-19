# MCP Stdio Server

Stdio-based MCP server communicates via stdin/stdout. This is suitable for local use if the code base is anyways installed.

For available tools, see [MCP Tools](../reference/mcp.md).

## Running

```bash
# Via CLI
kb-server-stdio

# Or directly
python -m kb_mcp.server.mcp_stdio
```

## Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kb-mcp": {
      "command": "/path/to/your/.venv/bin/kb-server-stdio"
    }
  }
}
```

## Environment

The stdio server loads environment variables from `.env` in the project root. So the default database connection and other settings are configured there. See [Installation Instructions](https://github.com/corrodis/kb-mcp#installation) for setup instructions. 

