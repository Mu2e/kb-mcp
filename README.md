# test-mcp

Minimal streamable-http MCP server with GitHub OAuth authentication support.

The server runs locally on your machine. For clients like Claude Desktop that require a public URL, we use [ngrok](https://ngrok.com) to create a secure tunnel.

## Quick Start

```bash
# Install dependencies (see docs/server/install.md)
# For server with KB support (typical installation):
pip install -e ".[server,kb]"

# Or install just what you need:
# pip install -e ".[server]"  # Server only
# pip install -e ".[kb]"       # Knowledge base only
# pip install -e ".[parser]"   # Parser only

# Setup GitHub OAuth App (see docs/server/install.md)
# Then configure environment
cp .env.example .env
# Edit .env with your GitHub OAuth credentials

# Generate SSL certificates
mkdir -p certs
openssl req -x509 -newkey rsa:4096 -nodes -keyout certs/key.pem -out certs/cert.pem -days 365

# Start server and tunnel
./scripts/start.sh

# Stop server and tunnel
./scripts/stop.sh
```

## Documentation

Documentation is organized by module to mirror the code structure:

### Server (`test_mcp.server`)
- [docs/server/install.md](docs/server/install.md) - Installation and setup
- [docs/server/configuration.md](docs/server/configuration.md) - Configuration options
- [docs/server/api-keys.md](docs/server/api-keys.md) - API key authentication
- [docs/server/clients.md](docs/server/clients.md) - Client integration (Claude Desktop, Cursor, Cline, MCP Inspector, curl)
- [docs/server/docker.md](docs/server/docker.md) - Docker deployment
- [docs/server/deploy-cloudrun.md](docs/server/deploy-cloudrun.md) - Google Cloud Run deployment
- [docs/server/admin-api.md](docs/server/admin-api.md) - Admin API for managing API keys on Cloud Run

### Knowledge Base (`test_mcp.kb`)
- Coming soon...

### Parser (`test_mcp.parser`)
- Coming soon...

## Requirements

- Python 3.10+
- [ngrok](https://ngrok.com) (for public tunnel)
- screen (for background processes)
