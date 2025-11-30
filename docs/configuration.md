# Configuration

All configuration is done via environment variables in the `.env` file.

## Server Configuration

### BASE_URL
The public URL where your server is accessible.

```bash
BASE_URL=https://your-ngrok-url.ngrok-free.app
```

### PORT
Local port the server binds to (default: 8443).

```bash
PORT=8443
```

## Logging Configuration

### LOG_LEVEL
Log level for third-party libraries (httpx, httpcore, mcp, etc).

Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

```bash
LOG_LEVEL=INFO
```

### MCP_LOG_LEVEL
Log level for your code (test_mcp.server, test_mcp.oauth).

Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

```bash
MCP_LOG_LEVEL=INFO
```

## GitHub OAuth Configuration

### GITHUB_CLIENT_ID
Your GitHub OAuth App Client ID.

```bash
GITHUB_CLIENT_ID=Ov23liXXXXXXXXXX
```

### GITHUB_CLIENT_SECRET
Your GitHub OAuth App Client Secret.

```bash
GITHUB_CLIENT_SECRET=your_secret_here
```

## Authorization Configuration

### GITHUB_REQUIRED_REPO
Restrict access to users who have access to a specific GitHub repository.

Format: `owner/repo`

```bash
GITHUB_REQUIRED_REPO=myorg/myrepo
```

Leave empty to allow all authenticated GitHub users:

```bash
GITHUB_REQUIRED_REPO=
```

## Web Interface Authentication

### REQUIRE_WEB_AUTH
Enable or disable authentication for web interfaces (`/admin` and `/web`).

```bash
REQUIRE_WEB_AUTH=true  # Enable authentication (production)
REQUIRE_WEB_AUTH=false # Disable authentication (development only)
```

**Default:** `true`

**Important:** Only set to `false` for local development. Always use `true` in production deployments.

When disabled:
- `/admin` and `/web` are accessible without GitHub OAuth login
- A development warning banner is displayed on both pages
- Session username is set to `dev-user` with full admin access
- MCP endpoints (`/mcp`) still require authentication (OAuth or API key)

### WEB_SESSION_TIMEOUT
Session timeout in seconds for web interfaces (`/admin` and `/web`).

```bash
WEB_SESSION_TIMEOUT=86400  # 24 hours (default)
WEB_SESSION_TIMEOUT=3600   # 1 hour
WEB_SESSION_TIMEOUT=604800 # 7 days
```

**Default:** `86400` (24 hours)

**Behavior:** Uses **sliding expiration** - the timeout is extended on every request. If a user is actively using the interface, their session won't expire. Sessions expire only after being inactive for the configured duration.

Expired sessions are automatically cleaned up on the next request attempt.

### WEB_REVERIFY_INTERVAL
How often to re-verify GitHub permissions for active sessions (in seconds).

```bash
WEB_REVERIFY_INTERVAL=3600  # 1 hour (default)
WEB_REVERIFY_INTERVAL=1800  # 30 minutes
WEB_REVERIFY_INTERVAL=7200  # 2 hours
```

**Default:** `3600` (1 hour)

**Behavior:** On each request, if more than this interval has passed since the last verification, the session will:
1. Re-check with GitHub to verify the user still exists
2. Re-verify repository access permissions
3. Re-verify admin permissions (for admin sessions)

If any verification fails (user revoked, permissions changed, token invalid), the session is immediately invalidated and the user must log in again.

**Admin pages (`/admin`) always re-verify on every request** regardless of this interval, similar to MCP OAuth tokens. This provides maximum security for sensitive operations (creating/revoking API keys).

**Regular web pages (`/web`)** use the configured interval (default: 1 hour) to reduce GitHub API calls while maintaining security.

## API Key Configuration

### API_KEYS_FILE
Path to API keys file (default: `data/api_keys.json`).

```bash
API_KEYS_FILE=data/api_keys.json
```

See [API Keys](api-keys.md) for usage details.

## Audit Logging Configuration

### AUDIT_LOG_FILE
Path to audit log file. When set, all tool calls are logged with username, tool name, and arguments.

```bash
AUDIT_LOG_FILE=logs/audit.log
```

Leave empty to disable audit logging:

```bash
AUDIT_LOG_FILE=
```

Audit log format (JSON):

```
2025-01-26 10:30:45 - [AUDIT] {"timestamp": "2025-01-26T10:30:45.123456", "username": "octocat", "tool": "generate_html", "arguments": {"title": "Hello", "content": "World"}}
```

## Example Configuration

See [.env.example](../.env.example) for a complete example configuration file.
