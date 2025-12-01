# CLI Tools

This project provides several command-line tools for different modules.

## Available CLI Tools

### Knowledge Base CLI (`kb`)

Manage the knowledge base: add documents, retrieve documents, and manage sources.

**See:** [KB CLI Documentation](kb/cli.md)

**Quick examples:**
```bash
# Add a document
kb add document.pdf --source-id mu2e-docdb

# Get a document
kb get mu2e-docdb_doc123
```

### Parser CLI (`kb-parse`)

Parse documents and generate image descriptions.

**See:** [Parser CLI Documentation](parser/cli.md)

**Quick examples:**
```bash
# Parse a document
kb-parse document.pdf

# Generate image description
kb-parse image test.png
```

### Server CLI (`test-mcp-manage-keys`)

Manage API keys for server authentication.

**See:** [API Keys Documentation](server/api-keys.md)

**Quick examples:**
```bash
# Generate an API key
test-mcp-manage-keys generate username "Description"

# List API keys
test-mcp-manage-keys list
```

## See Also

- [Parser CLI](parser/cli.md) - Detailed parser CLI documentation
- [KB CLI](kb/cli.md) - Detailed knowledge base CLI documentation
- [API Keys](server/api-keys.md) - API key management CLI

