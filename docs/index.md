# kb-mcp Documentation

A knowledge base and retrieval system for the **Mu2e experiment** at Fermilab, accessed via MCP (Model Context Protocol). Provides RAG-based question answering over Mu2e documentation, technical reports, and source code.

## Quick Start

**Using the KB:**

- [Connect an MCP client](guides/mcp-clients.md) — Use the KB through Claude Desktop, Cline, or other MCP clients
- [Web Interface](guides/web-interface.md) — Browser-based management and testing

**Running the server:**

- [Installation](guides/installation.md) — Install and configure kb-mcp
- [MCP Server Setup](guides/mcp-setup.md) — Run as stdio or remote MCP server
- [NERSC Setup](guides/nersc.md) — Quick setup on NERSC systems

## Data Sources

The system ingests Mu2e content from four sources:

| Source | Importer | Content |
|---|---|---|
| Mu2e Wiki | `kb-import wiki` | 306 wiki pages (procedures, subsystem docs, tutorials) |
| Mu2e DocDB | `kb-import docdb` | ~56,000 technical documents (PDFs, presentations) plus 299 topics, 860 authors, 203 events, 28 groups, 19 doc-types as graph nodes |
| INSPIRE-HEP | `kb-import inspire` | Physics papers via the INSPIRE search API |
| Offline repo | `kb-import code` | C++, FHiCL, and Python source files parsed with tree-sitter |

## Guides

- [Installation](guides/installation.md) — Setup and configuration
- [Adding Documents](guides/adding-documents.md) — Importing documents and source code
- [MCP Setup](guides/mcp-setup.md) — Running MCP servers (stdio and remote)
- [MCP Clients](guides/mcp-clients.md) — Connecting Claude Desktop, Cline, etc.
- [Web Interface](guides/web-interface.md) — Using the web UI
- [CLI Usage](guides/cli.md) — Command-line tools
- [Evaluation Workflows](guides/evaluation.md) — Measuring retrieval quality
- [Database Schema](guides/database.md) — Understanding the data models
- [Deployment](guides/deployment.md) — Docker and Cloud Run deployment
- [Extending kb-mcp](guides/extending-kb.md) — Custom chunking, embedders, importers
- [NERSC](guides/nersc.md) — NERSC-specific instructions

## API Reference

- [Configuration](reference/config.md) — Environment variables and settings
- [Knowledge Base (KB)](reference/kb.md) — Core KB functionality
  - [Search](reference/kb/search.md) — Semantic, fulltext, and hybrid search
  - [Knowledge Graph](reference/kb/graph.md) — Graph extraction and queries
  - [Evaluation](reference/kb/evaluation.md) — Retrieval quality measurement
- [Server](reference/server.md) — MCP and web server
- [Importer](reference/importer.md) — External source imports
- [Parser](reference/parser.md) — Document and code parsing
- [Summary](reference/summary.md) — Document summarization
- [Eval Utils](reference/eval-utils.md) — Question generation and answer judging

## Architecture

```
External Sources → imports/ → parser/ → kb/documents → kb/embedding (chunk+embed) → kb/search
                                                      → summary/     (summarize)
                                                      → kb/graph     (extract relations)
```

**Knowledge Base Core** (database-dependent):

- **[kb](reference/kb.md)** — Document storage, chunking, embeddings, hybrid search, knowledge graph, evaluation
- **[server](reference/server.md)** — MCP server (FastMCP) + Starlette web UI + OAuth
- **[imports](reference/importer.md)** — Source importers: MediaWiki, DocDB, INSPIRE-HEP, code repositories

**Standalone Components** (no database):

- **[parser](reference/parser.md)** — Document parsing (PDF, DOCX, PPTX, Excel, HTML) + code parsing (C++, FHiCL, Python via tree-sitter). PDFs default to IBM Docling (DocLayNet + TableFormer); CUDA-accelerated when available.
- **[summary](reference/summary.md)** — LLM-based summarization
- **[eval_utils](reference/eval-utils.md)** — Question generation and LLM-as-judge evaluation
