# Configuration

This document covers **shared configuration** used across multiple modules. For module-specific configuration, see:

- [Server Configuration](server/configuration.md) - Server, OAuth, web interfaces
- [Parser Configuration](parser/configuration.md) - Document parsing and image processing
- [Knowledge Base Configuration](kb/configuration.md) - Database and storage

All configuration is done via environment variables in the `.env` file.

## Shared Configuration

### DATA_DIR
Base directory for application data (API keys, session storage, databases, etc.).

```bash
DATA_DIR=data
```

**Default:** `data`

**Used by:**
- Server: API keys file, session storage
- Knowledge Base: SQLite database path (if not using PostgreSQL)

### Logging Configuration

#### LOG_LEVEL
Log level for third-party libraries (httpx, httpcore, mcp, etc).

Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

```bash
LOG_LEVEL=INFO
```

**Default:** `INFO`

#### MCP_LOG_LEVEL
Log level for your code (e.g., `test_mcp.server.*`, `test_mcp.kb.*`, `test_mcp.parser.*`).

Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

```bash
MCP_LOG_LEVEL=INFO
```

**Default:** Uses `LOG_LEVEL` if not set

## LLM Configuration

These settings are used by the parser module for generating image descriptions.

### OPENAI_API_KEY
OpenAI API key for LLM-based image description generation.

```bash
OPENAI_API_KEY=sk-...
```

**Required:** Only if using LLM image descriptions (see [Parser Configuration](parser/configuration.md))

### OPENAI_BASE_URL
Custom base URL for OpenAI-compatible API (e.g., for local models or alternative providers).

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
```

**Default:** Uses OpenAI's default URL if not set

**Note:** Leave empty to use OpenAI's default API endpoint.

## Example Configuration

See [.env.example](../.env.example) for a complete example configuration file.

