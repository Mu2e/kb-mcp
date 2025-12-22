# kb-mcp Documentation

A knowledge base system designed to be **used** via MCP (Model Context Protocol) with a web interface for testing and administration, and an evaluation suite.

## Quick Start

**Using the KB:**

- [Connect an MCP client](guides/mcp-clients.md) - Use the KB through Claude Desktop, Cline, or other MCP clients
- [Web Interface](guides/web-interface.md) - Browser-based management and testing

**Running the server:**

- [NERSC Setup](guides/nersc.md) - Quick setup on NERSC systems using `nersc_setup.sh`
- [Installation](guides/installation.md) - Install and configure kb-mcp
- [MCP Server Setup](guides/mcp-setup.md) - Run as stdio or remote MCP server


## Documentation

### Guides

- [Installation](guides/installation.md) - Setup and configuration
- [MCP Setup](guides/mcp-setup.md) - Running MCP servers (stdio and remote)
- [MCP Clients](guides/mcp-clients.md) - Connecting Claude Desktop, Cline, etc.
- [Web Interface](guides/web-interface.md) - Using the web UI
- [CLI Usage](guides/cli.md) - Command-line tools
- [Evaluation Workflows](guides/evaluation.md) - Testing retrieval quality
- [Database Schema](guides/database.md) - Understanding the data models
- [Deployment](guides/deployment.md) - Docker and Cloud Run deployment

### API Reference

- [Configuration](reference/config.md) - Environment variables and settings
- [Knowledge Base (KB)](reference/kb.md) - Core KB functionality
- [KB Server](reference/server.md) - MCP and web server
- [KB Importer](reference/importer.md) - External source imports
- [Parser](reference/parser.md) - Document parsing
- [Summary](reference/summary.md) - Document summarization
- [Eval Utils](reference/eval-utils.md) - Evaluation utilities

## Architecture

**Knowledge Base Core** (database-dependent):

- **[kb](reference/kb.md)** - Document storage, chunking, embeddings, search
- **[server](reference/server.md)** - MCP and web interfaces
- **[imports](reference/importer.md)** - Import from external sources (INSPIRE-HEP, etc.)

**Standalone Components** (no database dependencies):

- **[parser](reference/parser.md)** - Document-to-text extraction (PDF, DOCX, PPTX, Excel)
- **[summary](reference/summary.md)** - LLM-based summarization
- **[eval_utils](reference/eval-utils.md)** - Question generation and answer judging