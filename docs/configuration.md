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
