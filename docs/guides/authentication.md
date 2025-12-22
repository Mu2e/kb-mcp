# Authentication

The kb-mcp server supports multiple authentication methods for different use cases:

- **OAuth** (GitHub or Globus) - For both MCP clients and web interface
- **API Keys** - For MCP clients only (always available), not for the web part
- **Disabled** - Development mode only (localhost binding)

## Authentication Modes

### 1. OAuth Authentication (GitHub or Globus)

OAuth provides user-based authentication for both MCP clients and the web interface. The authentification redirects to a oauth provider (Globus or GitHub).

**Requirements:**
- Configure **one** OAuth provider (Globus or GitHub)
- Set client ID and secret
- (Optional) Restrict access to specific repository/group

**Setup:**

#### GitHub OAuth

1. Create a GitHub OAuth App at [GitHub Developer Settings](https://github.com/settings/developers)
2. Set the callback URL to: `{BASE_URL}/oauth/callback`
3. Configure in `.env`:
   ```bash
   GITHUB_CLIENT_ID=your-client-id
   GITHUB_CLIENT_SECRET=your-client-secret
   GITHUB_REQUIRED_REPO=owner/repo  # Optional: restrict to specific repo
   ```

#### Globus OAuth

1. Create a Globus OAuth App at [Globus Developer Console](https://developers.globus.org/)
2. Set the callback URL to: `{BASE_URL}/oauth/callback`
3. Configure in `.env`:
   ```bash
   GLOBUS_CLIENT_ID=your-client-id
   GLOBUS_CLIENT_SECRET=your-client-secret
   GLOBUS_REQUIRED_GROUP=group-uuid  # Optional: restrict to specific group
   ```

**Important:**
- Only configure **one** OAuth provider. If both are set, the server will raise an error.
- The web interface **requires** OAuth - API keys cannot be used for web authentication.

### 2. API Key Authentication

API keys are always available and can be used for MCP client authentication. They are useful for:

**Generating API Keys:**
For details also see [API Keys](api-keys.md).

**Using API Keys:**

Include the API key in the `Authorization` header:

```
Authorization: Bearer sk_your_api_key_here
```

See [API Keys Guide](api-keys.md) for detailed instructions.

### 3. No Authentication (Development Only)

For local development, you can disable authentication entirely:

```bash
DISABLE_AUTH=true
```

## Configuration Reference

For complete configuration details, see the [Configuration Reference](../reference/config.md). Key authentication-related functions:

- **`get_auth_config()`** - All authentication settings (OAuth, sessions, timeouts, API keys)
- **`get_github_oauth_config()`** - GitHub OAuth settings
- **`get_globus_oauth_config()`** - Globus OAuth settings

All settings use environment variables with defaults as documented in `config.py`. See `.env.example` for a complete list of available settings.

## Related Documentation

- [API Keys Guide](api-keys.md) - Detailed API key management
- [Installation Guide](installation.md) - Initial setup including OAuth
- [Configuration Reference](../reference/config.md) - All configuration options

