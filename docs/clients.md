# MCP Client Integration

How to connect different MCP clients to your server.

## Claude Desktop

Claude Desktop connects to remote MCP servers through **Connectors**.

**Important**: The OAuth flow is handled by claude.ai (not the desktop app), which means:
- Your server must be accessible on the public internet
- We use ngrok to create a secure tunnel to your local server

**Authentication**: OAuth (GitHub)

### Setup

1. Start server with ngrok tunnel:
   ```bash
   ./scripts/start.sh
   ```

2. Update `.env` with the ngrok URL shown by the start script

3. Update your GitHub OAuth App settings:
   - Homepage URL: `https://your-ngrok-url`
   - Authorization callback URL: `https://your-ngrok-url/oauth/github/callback`

4. In Claude Desktop:
   - Go to **Connectors**
   - Click **Add custom connector**
   - Name: `test-mcp`
   - URL: `https://your-ngrok-url/mcp`
   - Click **Connect**
   - Your browser will open for GitHub OAuth authentication
   - Authorize the app

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
