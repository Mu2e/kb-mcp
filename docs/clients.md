# MCP Client Integration

How to connect different MCP clients to your server.

## Claude Desktop

Claude Desktop connects to remote MCP servers through **Connectors**.

**Authentication**: OAuth (GitHub)

**Important**: The OAuth flow is handled by claude.ai, which means your server must be accessible on the public internet. Deploy to [Cloud Run](deploy-cloudrun.md) for production, or use ngrok for local development (see [Installation](install.md)).

### Setup

1. In Claude Desktop:
   - Go to **Connectors**
   - Click **Add custom connector**
   - Name: `test-mcp`
   - URL: `https://your-server-url/mcp`
     - Cloud Run: `https://mcp.example.com/mcp`
     - Local (ngrok): `https://your-ngrok-url/mcp`
   - Click **Connect**
   - Your browser will open for GitHub OAuth authentication
   - Authorize the app

**Note**: For local development with ngrok, the URL changes on each restart. Update the connector URL accordingly.

## Cursor IDE

Cursor connects to MCP servers via a local configuration file.

**Authentication**: API Key (see [API Keys](api-keys.md))

### Setup

1. Install mkcert and generate certificates (see [Installation](install.md#5-ssl-certificates))

   **Important**: Cursor requires trusted certificates. Self-signed certificates (e.g., from openssl) will not work. You must use mkcert to create locally-trusted certificates.

2. Generate an API key:
   ```bash
   test-mcp-manage-keys generate your-username "Cursor IDE"
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
       "test-mcp": {
         "url": "https://localhost:8443/mcp",
         "headers": {
           "Authorization": "Bearer YOUR_API_KEY"
         }
       }
     }
   }
   ```

5. Restart Cursor

## curl / HTTP Clients

For testing or custom integrations, use API keys.

See [API Keys](api-keys.md#testing-with-curl) for curl examples.
