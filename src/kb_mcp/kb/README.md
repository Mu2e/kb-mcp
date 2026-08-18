# Knowledge Base Module

Core document storage and retrieval for the Mu2e knowledge base. Uses SQLAlchemy with PostgreSQL (production) or SQLite (development).

## Features

- Document storage with rich metadata and source tracking
- Hybrid search: semantic (vector) + full-text with Reciprocal Rank Fusion
- Cross-encoder reranking for improved precision
- Query routing by question type (factual, procedural, synthesis, figure, lookup)
- Knowledge graph with Mu2e-specific ontology (subsystems, components, physics processes)
- Evaluation pipeline with hand-curated benchmarks and LLM-as-judge
- CLI, web interface, and MCP tool access

## Database Configuration

**SQLite (Development):**
```bash
export SQLITE_DB_PATH="data/kb.db"
# Or leave unset for default
```

**PostgreSQL (Production):**
```bash
export DB_HOST="localhost"
export DB_PORT="5432"
export DB_USER="user"
export DB_PASSWORD="password"
export DB_NAME="kb"
```

## Usage

### CLI

```bash
# Import documents
kb-import wiki --wiki-url https://mu2e-wiki.fnal.gov --skip-existing
kb-import docdb --query "recent:50" --skip-existing
kb-import code --path /path/to/Offline --repo-name Offline

# Search
kb search "tracker straw tube material" --max-results 5

# Knowledge graph
kb graph get-node --name "Tracker"
kb graph extract-all --source-id mu2e-wiki --max-documents 10

# Evaluation
kb eval load-benchmark
kb eval run --generation-id ID --max-results 5 --rerank
kb eval stats RUN_ID --use-judge

# Statistics
kb stats
```

### MCP Tools

Six tools available to MCP clients (Claude Desktop, etc.):

- `kb_search` — Hybrid search with optional reranking and query routing
- `kb_get` — Retrieve full document by ID
- `kb_get_image` — Retrieve image resources
- `kb_lookup_node` — Knowledge graph node lookup
- `kb_find_path` — Find paths between graph nodes
- `kb_node_relation_evidence` — Get evidence for graph relations

### Web Interface

Start the server with `kb-server` and visit the web UI for document browsing, search, graph exploration, and evaluation dashboards.

## Supported File Types

- PDF, Word (DOCX), PowerPoint (PPTX), Excel, HTML, plain text, Markdown
- C++ source files (`.cc`, `.hh`, `.h`) — parsed with tree-sitter
- FHiCL configuration files (`.fcl`) — parsed with regex
- Python files (`.py`) — parsed with tree-sitter
- Images (extracted from PDFs with LLM-generated descriptions)
