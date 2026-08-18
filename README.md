# kb-mcp

A knowledge base and retrieval system for the **Mu2e experiment** at Fermilab, accessed via MCP (Model Context Protocol). Provides RAG-based question answering over Mu2e documentation, technical reports, and source code through Claude Desktop or any MCP-compatible client.

## What It Does

Ask questions about the Mu2e experiment and get answers grounded in the actual documentation and code:

- **Documentation**: Mu2e Wiki pages, DocDB documents, INSPIRE-HEP papers, local PDF collections, meeting transcripts
- **Parsing**: PDF, PPTX, HTML/XHTML, and DOCX default to IBM Docling for structure-preserving Markdown — headings, tables, bullets, captions retained as a canonical `DoclingDocument` JSON artefact alongside the rendered text. PDF uses DocLayNet + TableFormer with CUDA when available; PPTX/DOCX/HTML use Docling's lighter format-specific readers. Pass `parser_name="legacy"` for the bespoke parser of any flipped MIME, `parser_name="pypdf2"`, `"marker"`, `"nougat"`, or `"azure"` for PDF alternatives. Plaintext, generic XML, and XLSX stay on bespoke parsers by design — Docling adds nothing structural for unstructured text and overfragments spreadsheets (one record per row).
- **Knowledge graph**: Mu2e-specific ontology with 20 node types and 26 relation verbs (experimental — extraction is invoked explicitly, not run automatically at import time).

## Quick Start

```bash
# Clone the repository
git clone https://github.com/HEP-KE/kb-mcp.git
cd kb-mcp

# (Recommended) Create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  

# Install dependencies
pip install -e .

# Configure (copy .env.example to .env and set OPENAI_API_KEY)
cp .env.example .env

# Start MCP server for Claude Desktop
kb-server-stdio

# Or start HTTP server with web interface
kb-server
```

## Importing Data

```bash
# Mu2e DocDB — documents (requires MU2E_DOCDB_USERNAME / MU2E_DOCDB_PASSWORD)
kb-import docdb --days 30 --skip-existing -v         # updated in the last 30 days
kb-import docdb --ids 51472 51500 -v                 # specific document IDs
kb-import docdb -q "calorimeter calibration" -v      # title/abstract/keyword search

# Mu2e Wiki
kb-import wiki --wiki-url https://mu2ewiki.fnal.gov --query all --skip-existing

# INSPIRE-HEP papers
kb-import inspire --query "find exp mu2e" --max-results 30

# Local PDF directory (register now, parse via the GPU pipeline later)
kb-import local-pdf /path/to/pdfs --source-id my-pdfs
```

## CLI Tools

| Command | Description |
|---|---|
| `kb-server` | MCP server (HTTP/HTTPS) with web interface |
| `kb-server-stdio` | MCP server (stdio) for Claude Desktop |
| `kb` | Knowledge base CLI (search, ingest, embed, graph, eval) |
| `kb-import` | Import from external sources (wiki, DocDB, INSPIRE, local PDFs) |
| `kb-parse` | Parse documents (PDF, DOCX, PPTX, Excel, text) |
| `kb-agent` | Recursive research agent |

## Architecture

```
External Sources → imports/ → parser/ → kb/documents → kb/embedding (chunk+embed) → kb/search
                                                      → summary/     (summarize)
                                                      → kb/graph     (extract relations)
```

**Search pipeline**: Hybrid search combines semantic (vector) and full-text retrieval via Reciprocal Rank Fusion, with optional cross-encoder reranking and query routing.

**Knowledge Base Core** (database-dependent):

- **kb/** — Document storage, chunking, embeddings, hybrid search, knowledge graph, evaluation
- **server/** — MCP server (FastMCP) + Starlette web UI + OAuth (GitHub/Globus)
- **imports/** — Source importers: DocDB (FNAL SSO auth), MediaWiki, INSPIRE-HEP, local PDF directories

**Standalone Components** (no database):

- **parser/** — Document parsing (PDF via Docling/PyPDF2/Marker/Nougat/Azure, DOCX, PPTX, Excel, HTML, text)
- **summary/** — LLM-based summarization
- **eval_utils/** — Question generation and LLM-as-judge answer evaluation

## Documentation

```bash
pip install -e ".[doc]"
mkdocs serve
# Open http://localhost:8000
```

### Guides

- [Installation](docs/guides/installation.md) — Setup and configuration
- [MCP Setup](docs/guides/mcp-setup.md) — Running MCP servers (stdio and remote)
- [MCP Clients](docs/guides/mcp-clients.md) — Connecting Claude Desktop, Cline, etc.
- [Web Interface](docs/guides/web-interface.md) — Browser-based management UI
- [CLI Usage](docs/guides/cli.md) — Command-line tools
- [Adding Documents](docs/guides/adding-documents.md) — Importing documents and code
- [Evaluation](docs/guides/evaluation.md) — Measuring retrieval quality
- [Database Schema](docs/guides/database.md) — Data models
- [Deployment](docs/guides/deployment.md) — Docker and Cloud Run deployment
- [Extending kb-mcp](docs/guides/extending-kb.md) — Custom chunking, embedders, importers

### API Reference

- [Configuration](docs/reference/config.md) — Environment variables and settings
- [Knowledge Base](docs/reference/kb.md) — Core KB functionality
- [Server](docs/reference/server.md) — MCP and web server
- [Importer](docs/reference/importer.md) — External source imports
- [Parser](docs/reference/parser.md) — Document and code parsing
- [Summary](docs/reference/summary.md) — Summarization
- [Eval Utils](docs/reference/eval-utils.md) — Evaluation utilities

## Requirements

- Python 3.10+
- SQLite (development) or PostgreSQL with pgvector (production)
- OpenAI-compatible API key (for summarization and graph extraction). Supports LiteLLM proxies via `OPENAI_BASE_URL`.
- Embeddings can use local sentence-transformers (`EMBEDDING_PROVIDER=st`, no API cost) or OpenAI

## License

MIT
