# Knowledge Base Module

This module provides document storage and retrieval functionality using SQLAlchemy with PostgreSQL (or SQLite for development).

## Features

- Document storage with metadata
- Source management
- Text extraction from various document formats
- CLI interface for document management
- Web interface for browsing documents
- MCP tool integration

## Setup

### Database Configuration

The knowledge base uses SQLAlchemy and supports both PostgreSQL and SQLite.

**PostgreSQL (Production):**
```bash
export DB_URL="postgresql://user:password@localhost:5432/kb"
```

**SQLite (Development):**
```bash
export DB_URL="sqlite:///./kb.db"
# Or leave unset to use default SQLite database
```

### Document Processing

Install optional dependencies for document processing:

```bash
pip install -e ".[doc-processing]"
```

This installs:
- `PyPDF2` - PDF text extraction
- `python-docx` - Word document text extraction
- `beautifulsoup4` - HTML parsing

### Using mu2eDocChat Parsers

The pipeline can use parsers from [mu2eDocChat](https://github.com/corrodis/mu2eDocChat) if available. The system will automatically detect and use them, falling back to built-in parsers if not found.

**Option 1: Install from GitHub**
```bash
pip install git+https://github.com/corrodis/mu2eDocChat.git
```

**Option 2: Clone and install locally**
```bash
git clone https://github.com/corrodis/mu2eDocChat.git
cd mu2eDocChat
pip install -e .
```

**Option 3: Add to PYTHONPATH**
```bash
export PYTHONPATH=/path/to/mu2eDocChat:$PYTHONPATH
```

The pipeline will automatically try to use mu2eDocChat parsers first, and fall back to built-in parsers if they're not available or if there's an error.

## Usage

### CLI

```bash
# Add a source
kb source add mu2e-docdb --name "Mu2e DocDB" --base-uri "https://mu2e-docdb.fnal.gov"

# Add a document
kb add document.pdf --source-id mu2e-docdb --doc-id 1234

# Get a document
kb get mu2e-docdb_1234

# View statistics
kb stats
```

### Python API

```python
from kb_mcp.kb import add, get, add_source

# Add a source
source = add_source(
    source_id="mu2e-docdb",
    name="Mu2e DocDB",
    base_uri="https://mu2e-docdb.fnal.gov"
)

# Add a document
doc = add({
    "source_id": "mu2e-docdb",
    "doc_id": "1234",
    "uri": "https://mu2e-docdb.fnal.gov/docdb/1234",
    "source_type": "application/pdf",
    "text": "Document content...",
})

# Get a document
doc = get("mu2e-docdb_1234")  # Using source_id_doc_id format
doc = get(uuid="abc-123-def")  # Using UUID
doc = get(source_id="mu2e-docdb", doc_id="1234")  # Using explicit parameters
```

### Web Interface

Visit `/kb` in your browser for an overview of all documents, or `/kb/doc/<uuid>` to view a specific document.

### MCP Tool

The `kb_get_document` tool is available to MCP clients (Claude Desktop, Cursor, etc.) for retrieving documents.

## Document Identifiers

Documents can be retrieved using several identifier formats:

1. **UUID**: The unique database ID (36 characters with dashes)
2. **source_id_doc_id**: Combined format like `mu2e-docdb_1234`
3. **Explicit parameters**: `source_id` and `doc_id` separately

## Supported File Types

- PDF (`application/pdf`)
- Word documents (`.doc`, `.docx`)
- HTML (`text/html`, `application/xhtml+xml`)
- Plain text (`text/*`)
- Markdown (`text/markdown`)

Additional formats can be supported by installing mu2eDocChat parsers or extending the pipeline.

