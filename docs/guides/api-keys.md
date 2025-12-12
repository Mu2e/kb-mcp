# API Keys

API keys provide authentication as an alternative to GitHub OAuth.

## Generating API Keys

```bash
kb-server-manage-keys generate <username> [description]
```

Example:

```bash
kb-server-manage-keys generate alice "CI pipeline key"
```

Output:

```
Generated API key for alice:

  sk_a1b2c3d4e5f6...

IMPORTANT: Save this key now - it will not be shown again!

Description: CI pipeline key
Username: alice
```

## Listing API Keys

```bash
kb-server-manage-keys list
```

Example output:

```
Found 2 API key(s):

Key: sk_a1b2c3...d4e5f6g7
  Username: alice
  Created: 2025-01-26T10:30:00.123456
  Description: CI pipeline key

Key: sk_x9y8z7...w6v5u4t3
  Username: bob
  Created: 2025-01-26T11:45:00.789012
```

## Revoking API Keys

```bash
kb-server-manage-keys revoke <api_key>
```

Example:

```bash
kb-server-manage-keys revoke sk_a1b2c3d4e5f6...
```

## Using API Keys

Include API keys in the `Authorization` header:

```
Authorization: Bearer sk_your_api_key_here
```

## Testing with curl

```bash
curl -X POST https://127.0.0.1:8443/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  --insecure \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

## Configuration

See [Configuration](../reference/config.md) for details on changing the API keys file location.
