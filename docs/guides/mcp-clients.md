# MCP Clients

This page describes how to connect different MCP clients to a remote MCP server. This MCP server can be started as [local (stdio)](mcp-stdio.md) or deployed remotely. 


## Claude Desktop

Claude Desktop can connect via **stdio** (local) or **Connectors** (remote).

### Option 1: Stdio (Local, Recommended)

The simplest setup - runs locally without authentication.

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

See [MCP Stdio](mcp-stdio.md) for details.

### Option 2: Remote Connector (OAuth)

For connecting to a remote server via OAuth.

**Authentication**: OAuth (GitHub)

**Important**: The OAuth flow is handled by claude.ai, which means your server must be accessible on the public internet. Deploy to [Cloud Run](deployment.md) for production, or use ngrok for local development (see [Installation](installation.md)).

1. In Claude Desktop:
   - Go to **Connectors**
   - Click **Add custom connector**
   - Name: `kb-mcp`
   - URL: `https://your-server-url/mcp`
     - Cloud Run: `https://sld.example.com/mcp`
   - Click **Connect**
   - Your browser will open for OAuth authentication (Globus or GitHub, depending on server configuration)
   - Authorize the app

**Note**: For local development with ngrok, the URL changes on each restart. Update the connector URL accordingly.

## ollmcp

ollmcp connects to MCP servers via a local configuration file.

**Authentication**: API Key (see [API Keys](api-keys.md))

**Note**: ollmcp doesn't support OAuth2 flows at the moment, so API keys are required. It supports tools but not resources.

### Setup

1. Generate an API key:
   ```bash
   kb-server-manage-keys generate your-username "ollmcp"
   ```
   Save the generated key.

2. Create or edit your ollmcp config file at `~/.config/ollmcp/mcp-servers/config.json`:
   ```json
   {
     "mcpServers": {
       "kb-mcp": {
         "type": "streamable_http",
         "url": "https://your-server-url/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_API_KEY"
         }
       }
     }
   }
   ```

3. Start ollmcp:
   ```bash
   ollmcp -j ~/.config/ollmcp/mcp-servers/config.json
   ```

## Cursor IDE

Cursor connects to MCP servers via a local configuration file.

**Authentication**: API Key (see [API Keys](api-keys.md))

### Local Development

1. Install mkcert and generate certificates (see [Installation](installation.md#5-ssl-certificates))

   **Important**: Cursor requires trusted certificates. Self-signed certificates (e.g., from openssl) will not work. You must use mkcert to create locally-trusted certificates.

2. Generate an API key:
   ```bash
   kb-server-manage-keys generate your-username "Cursor IDE"
   ```
   Save the generated key.

3. Start server locally:
   ```bash
   ./scripts/start.sh
   ```

4. Add to your Cursor settings file (`.cursor/mcp.json`):
   ```json
   {
     "mcpServers": {
       "kb-mcp": {
         "url": "https://localhost:8443/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_API_KEY"
         }
       }
     }
   }
   ```

5. Restart Cursor

### Cloud Run Deployment

To connect Cursor to your Cloud Run deployment:

1. Generate an API key (run locally or via Cloud Shell):
   ```bash
   kb-server-manage-keys generate your-username "Cursor IDE"
   ```

2. Add to your Cursor settings file (`.cursor/mcp.json`):
   ```json
   {
     "mcpServers": {
       "kb-mcp-local": {
         "url": "https://localhost:8443/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_LOCAL_API_KEY"
         }
       },
       "kb-mcp-cloud": {
         "url": "https://mcp.scorrodi.dev/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_CLOUD_API_KEY"
         }
       }
     }
   }
   ```

3. Restart Cursor

## Cline (VS Code Extension)

Cline connects to MCP servers via VS Code settings.

**Authentication**: OAuth (Globus or GitHub) or API Key

### Setup

1. Open VS Code Command Palette (`Cmd+Shift+P` / `Ctrl+Shift+P`)
2. Search for "Cline: Open MCP Settings"
3. Add your server configuration:

**For Cloud Run (OAuth):**
```json
{
  "mcpServers": {
    "kb-mcp": {
      "url": "https://mcp.scorrodi.dev/mcp",
      "type": "streamableHttp"
    }
  }
}
```

**For local development or API key authentication:**
```json
{
  "mcpServers": {
    "kb-mcp": {
      "url": "https://localhost:8443/mcp",
      "type": "streamableHttp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

4. Reload VS Code

## MCP Inspector

The official [MCP Inspector](https://github.com/modelcontextprotocol/inspector) can test your server interactively.

```bash
npx @modelcontextprotocol/inspector
```

In the Inspector UI, select **HTTP transport** and configure:

- URL: `https://your-server-url/mcp` (Cloud Run or ngrok)
- Authorization: Use API key with Bearer <token>, or proxy through OAuth-enabled client

## curl / HTTP Clients

For testing or custom integrations, use API keys.

See [API Keys](api-keys.md#testing-with-curl) for curl examples.
