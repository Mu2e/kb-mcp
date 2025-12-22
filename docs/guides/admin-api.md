# Admin Interface

The admin interface provides a web-based UI for managing API keys.

**Security**: Requires OAuth authentication (Globus or Github, same credentials as MCP server access).

## Access

Simply navigate to the `/admin` endpoint:

**Local:** `https://localhost:8443/admin`
**Cloud Run:** `https://mcp.scorrodi.dev/admin`

## Authentication

1. Visit `/admin` in your web browser
2. You'll be redirected to OAuth (Globus or GitHib, depending on server configuration)
3. Log in with your OAuth provider
4. If you have **admin permissions** on the required repository/group (if configured), you'll be granted access
5. Your browser session will be maintained via a secure cookie

## Features

- **Generate API Keys**: Create new API keys for users
- **List API Keys**: View all existing API keys (usernames, descriptions, creation dates)
- **Revoke API Keys**: Revoke access for specific users
- **Logout**: End your admin session

## Security Notes

- Admin sessions are stored in-memory (sessions are lost on server restart)
- Sessions use secure, HTTP-only cookies
- OAuth (GitHub or Globus) is used for authentication
- **Admin permission required**: 
  - GitHub: If `GITHUB_REQUIRED_REPO` is configured, users must have admin permissions on that repository
  - Globus: Admin checks are not yet implemented (all authenticated users have access)
- All actions are logged for audit purposes
