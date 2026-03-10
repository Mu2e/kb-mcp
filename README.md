# kb-mcp

A knowledge base system designed to be **used** via MCP (Model Context Protocol) with a web interface for testing and administration, and an evaluation suite.

## Quick Start

**Installation:**

```bash
# Clone the repository
git clone https://github.com/HEP-KE/kb-mcp.git
cd kb-mcp

# (Recommended) Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  

# Install dependencies
pip install -e .
```

**Running the server:**

- [Installation Guide](docs/guides/installation.md) - Install and configure kb-mcp
- [MCP Server Setup](docs/guides/mcp-setup.md) - Run as stdio or remote MCP server

**Using the KB:**

- [Connect an MCP client](docs/guides/mcp-clients.md) - Use the KB through Claude Desktop, Cline, or other MCP clients
- [Web Interface](docs/guides/web-interface.md) - Browser-based management and testing

## Documentation

Full documentation is available in the [docs/](docs/) directory. To view locally:

```bash
pip install -e ".[docs]"
mkdocs serve
# Open http://localhost:8000 in your browser
```

### Guides

- [Installation](docs/guides/installation.md) - Setup and configuration
- [Adding Documents](docs/guides/adding-documents.md) - Import documents to your knowledge base
- [MCP Setup](docs/guides/mcp-setup.md) - Running MCP servers (stdio and remote)
- [MCP Clients](docs/guides/mcp-clients.md) - Connecting Claude Desktop, Cline, etc.
- [Web Interface](docs/guides/web-interface.md) - Using the web UI
- [CLI Usage](docs/guides/cli.md) - Command-line tools
- [Evaluation Workflows](docs/guides/evaluation.md) - Testing retrieval quality
- [Database Schema](docs/guides/database.md) - Understanding the data models
- [Deployment](docs/guides/deployment.md) - Docker and Cloud Run deployment
- [Extending kb-mcp](docs/guides/extending-kb.md) - Adding custom chunking strategies and embedding providers

### API Reference

- [Configuration](docs/reference/config.md) - Environment variables and settings
- [Knowledge Base (KB)](docs/reference/kb.md) - Core KB functionality
- [KB Server](docs/reference/server.md) - MCP and web server
- [KB Importer](docs/reference/importer.md) - External source imports
- [Parser](docs/reference/parser.md) - Document parsing
- [Summary](docs/reference/summary.md) - Document summarization
- [Eval Utils](docs/reference/eval-utils.md) - Evaluation utilities

## Architecture

**Knowledge Base Core** (database-dependent):

- **[kb](docs/reference/kb.md)** - Document storage, chunking, embeddings, search
- **[server](docs/reference/server.md)** - MCP and web interfaces
- **[imports](docs/reference/importer.md)** - Import from external sources (INSPIRE-HEP, etc.)

**Standalone Components** (no database dependencies):

- **[parser](docs/reference/parser.md)** - Document-to-text extraction (PDF, DOCX, PPTX, Excel)
- **[summary](docs/reference/summary.md)** - LLM-based summarization
- **[eval_utils](docs/reference/eval-utils.md)** - Question generation and answer judging

## Requirements

- Python 3.10+
- PostgreSQL (for production) or SQLite (for development)
- OpenAI-compatible LLM API (for embeddings and summarization)

## License

MIT
