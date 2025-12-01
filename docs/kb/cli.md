# Knowledge Base CLI

The `kb` command-line tool provides operations for managing the knowledge base.

## Installation

```bash
pip install -e ".[kb]"
```

## Commands

- `kb add <file>` - Add a document from a file
- `kb get <identifier>` - Retrieve a document
- `kb stats` - Show knowledge base statistics
- `kb source add <source_id>` - Add or update a source

## Examples

```bash
# Add a document
kb add document.pdf --source-id mu2e-docdb --doc-id doc123

# Get a document
kb get mu2e-docdb_doc123

# View statistics
kb stats

# Create a source
kb source add mu2e-docdb --name "Mu2e DocDB"
```

## Options

See `kb --help` for full option list.

## Configuration

See [KB Configuration](configuration.md) for database setup.
