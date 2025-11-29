# test-mcp

Minimal streamable-http MCP server with GitHub OAuth authentication support.

The server runs locally on your machine. For clients like Claude Desktop that require a public URL, we use [ngrok](https://ngrok.com) to create a secure tunnel.

## Quick Start

```bash
# Install dependencies (see docs/install.md)
pip install -e .

# Setup GitHub OAuth App (see docs/install.md)
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

- [docs/install.md](docs/install.md) - Installation and setup
- [docs/configuration.md](docs/configuration.md) - Configuration options
- [docs/api-keys.md](docs/api-keys.md) - API key authentication
- [docs/clients.md](docs/clients.md) - Client integration (Claude Desktop, Cursor, Cline, MCP Inspector, curl)
- [docs/docker.md](docs/docker.md) - Docker deployment
- [docs/deploy-cloudrun.md](docs/deploy-cloudrun.md) - Google Cloud Run deployment
- [docs/admin-api.md](docs/admin-api.md) - Admin API for managing API keys on Cloud Run

## Requirements

- Python 3.10+
- [ngrok](https://ngrok.com) (for public tunnel)
- screen (for background processes)
