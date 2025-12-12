# Documentation

This Knowledge Base (KB) is designed to be **used** through an MCP server. Examples of how to connect clients can be found in [MCP Clients](server/mcp_clients.md).

The KB is built around a SQL-based documentation [database](kb/database.md). It offers:

 - [MCP server](server/mcp.md): Available as [remote server](server/mcp_remote.md) or [local stdio-based server](server/mcp_stdio.md)
 - [Web-based management interface](server/web.md): for testing and administration
 - [Comprehensive CLI](cli/cli.md): command-line interface for administration

## Quick Start

 - [Connect an MCP client](server/mcp_clients.md) to a remote server
 - [Installation guide](install.md)
 - [Running the MCP server](server/mcp.md)
 - [Running the web server](server/web.md)

## Code Base

The codebase is [configured](module/config.md) through environment variables accessible via `kb_mcp.config`.

### Knowledge Base Core

These components integrate with the database:

 - [kb](api/kb.md) - Knowledge Base core (storage, chunking, embeddings, search)
 - [server](api/server.md) - Server interfaces (MCP and web) for the knowledge base
 - [imports](api/imports.md) - Utilities to import documents from external sources

### Standalone Components

Standalone tools that have **no database dependencies** and can be used independently:


 - [llm](api/llm.md) - OpenAI-compatible LLM interface
 - [parser](api/parser.md) - Document-to-text parsing (PDF, DOCX, etc.)
 - [summary](api/summary.md) - Document summarization and gist extraction
 - [eval_utils](api/eval.md) - Evaluation utilities (synthetic question generation, LLM-based judging)



