# Server Configuration

Configuration for the MCP server module (`test_mcp.server`).

For shared configuration (logging, data directory, LLM settings), see [Configuration](../configuration.md).

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

### HOST
Host address the server binds to (default: 127.0.0.1).

```bash
HOST=127.0.0.1
```

**Default:** `127.0.0.1`

### USE_HTTPS
Enable HTTPS for the server (default: true).

```bash
USE_HTTPS=true
```

**Default:** `true`

**Note:** When enabled, requires SSL certificates in `certs/key.pem` and `certs/cert.pem`.

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

### DISABLE_WEB_AUTH
Disable authentication for web interfaces (`/admin` and `/web`) for local development.

```bash
DISABLE_WEB_AUTH=false # Authentication enabled (default, production)
DISABLE_WEB_AUTH=true  # Disable authentication (development only)
```

**Default:** `false` (authentication is **enabled** by default)

**Important:** Only set to `true` for local development. Always use `false` (or omit) in production deployments.

When disabled (`DISABLE_WEB_AUTH=true`):
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
Path to API keys file (default: `{DATA_DIR}/api_keys.json`).

```bash
API_KEYS_FILE=data/api_keys.json
```

**Default:** `{DATA_DIR}/api_keys.json` (where `DATA_DIR` is from [shared configuration](../configuration.md))

See [API Keys](api-keys.md) for usage details.

## Session Storage Configuration

### SESSION_STORE_FIRESTORE
Use Google Cloud Firestore for session storage instead of local file storage.

```bash
SESSION_STORE_FIRESTORE=false  # Use local file storage (default)
SESSION_STORE_FIRESTORE=true   # Use Firestore
```

**Default:** `false`

**Note:** When enabled, requires Google Cloud credentials and the `gcp` optional dependency.

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
