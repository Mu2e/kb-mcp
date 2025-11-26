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

## Requirements

- Python 3.10+
- [ngrok](https://ngrok.com) (for public tunnel)
- screen (for background processes)
