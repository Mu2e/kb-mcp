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

Comprehensive documentation is available in the [docs/](docs/) directory:

### Quick Links
- [Architecture Overview](docs/ARCHITECTURE.md) - System architecture and design
- [Configuration Guide](docs/configuration.md) - Centralized configuration
- [Module Documentation](docs/modules/README.md) - Detailed module guides

### Module Guides
- [Server Module](docs/modules/server.md) - MCP server and web interface
- [Knowledge Base Module](docs/modules/kb.md) - Document storage and search
- [Parser Module](docs/modules/parser.md) - Document parsing
- [Evaluation Utilities](docs/modules/eval_utils.md) - QA generation and judging

### Server Documentation
- [Installation](docs/server/install.md) - Setup and configuration
- [Client Integration](docs/server/clients.md) - Claude Desktop, Cursor, etc.
- [API Keys](docs/server/api-keys.md) - API authentication
- [Docker](docs/server/docker.md) - Docker deployment
- [Cloud Run](docs/server/deploy-cloudrun.md) - Google Cloud deployment

## Requirements

- Python 3.10+
- [ngrok](https://ngrok.com) (for public tunnel)
- screen (for background processes)
