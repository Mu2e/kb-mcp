# MCP Server

The Model Context Protocol (MCP) is an open standard for connecting AI assistants to data sources and tools. Learn more at [modelcontextprotocol.io](https://modelcontextprotocol.io).

## Server Modes

This server supports two MCP modes:

### Stdio MCP Server

In stdio mode, the MCP client (e.g., Claude Desktop) starts the server process and communicates via standard input/output.

**Setup:** See [stdio MCP setup](mcp-stdio.md) for configuration details.

### Remote MCP Server (Streamable-HTTP)

In remote mode, the server runs independently and clients connect over HTTPS using the streamable-http transport.

**Setup:**

1. [Run the MCP server](installation.md) - Start the standalone server
2. [Connect a client](mcp-clients.md) - Configure your MCP client to connect to the remote server
