# Knowledge Base CLI

The `kb` command-line tool provides operations for managing the knowledge base.

## Installation

```bash
pip install -e ".[kb]"
```

## Quick Examples

```bash
# Add a document
kb add document.pdf --source-id mu2e-docdb --doc-id doc123

# Search documents
kb search "quantum mechanics"

# View statistics
kb stats

# Generate evaluation dataset
kb eval generate --num-questions 50

# Run evaluation
kb eval run --dataset-name test-dataset
```

## Command Reference

### Document Operations

- **add** - Add documents from files (PDF, DOCX, etc.)
- **get** - Retrieve document by ID
- **embed** - Chunk and embed a specific document
- **drop** - Delete a document
- **search** - Search documents using semantic search
- **similar** - Find documents similar to a given document

### Chunks, Embeddings & Sources

- **source** - Manage document sources
  - `source add` - Create or update a source
  - `source list` - List all sources
- **chunks** - Manage document chunks
  - `chunks list` - List chunks for a document
  - `chunks chunk` - Chunk a specific document
  - `chunks get` - Get chunk by ID
  - `chunks drop` - Delete chunks for a document
- **embedding** (alias: **emb**) - Manage embeddings
  - `embedding list` - List embedding configurations
  - `embedding embed` - Embed a specific chunk
  - `embedding embed-all` - Generate embeddings for all chunks that don't have them yet
  - `embedding get` - Get embedding by chunk ID
  - `embedding drop` - Delete embeddings for a chunk

### Evaluation & Benchmarking

- **eval** - Evaluation tools for testing search quality
  - `eval generate` - Generate synthetic evaluation questions
  - `eval audit` - Review and filter generated questions
  - `eval run` - Run evaluation experiment
  - `eval stats` - View evaluation results
  - `eval list` - List evaluation datasets and runs

### Tools & Statistics

- **tools** - Utility commands
  - `tools deduplicate` - Remove duplicate documents
  - `tools chunk-and-embed-all` - Chunk and embed all documents for a source
  - `tools summarize-all` - Generate summaries for all documents from a source
  - `tools list-tables` - List all database tables
  - `tools drop-table` - Drop a database table by name
- **stats** - Show knowledge base statistics
- **logs** - View processing logs
  - `logs search` - View search logs
  - `logs chunking` - View chunking logs
  - `logs parsing` - View parsing logs

## Common Workflows

### Adding Documents

```bash
# Add single document
kb add document.pdf --source-id papers --doc-id paper-001

# Add and automatically embed
kb add document.pdf --source-id papers --doc-id paper-001
kb embed papers_paper-001

# Add from directory
for file in docs/*.pdf; do
  kb add "$file" --source-id archive
done
```

### Batch Processing

```bash
# Generate summaries and create summary chunks (but don't embed them)
kb tools summarize-all archive

# Chunk and embed all documents without chunks
kb tools chunk-and-embed-all archive

# Chunk and embed without prepending gist (creates separate strategy)
kb tools chunk-and-embed-all archive --strategy tokens --no-gist

# Generate embeddings for all chunks without embeddings
# (useful when re-embedding with a different model)
kb embedding embed-all --source-id archive

# Embed only summary chunks
kb embedding embed-all --chunk-strategy summary

# Re-embed with a different model
kb embedding embed-all --source-id archive --provider openai --model text-embedding-3-large
```

### Running Evaluations

```bash
# Generate evaluation questions
kb eval generate --num-questions 100 --dataset-name my-eval

# Review questions (optional)
kb eval audit --dataset-name my-eval

# Run evaluation
kb eval run --dataset-name my-eval

# View results
kb eval stats --dataset-name my-eval
```

## Configuration

Configuration is managed through environment variables. See [configuration](../reference/config.md) for details.

Key environment variables:

- `DB_URL` - Database connection string
- `EMBEDDING_PROVIDER` - Embedding provider (st, openai, etc.)
- `EMBEDDING_MODEL` - Specific model to use
- `CHUNK_STRATEGY` - Chunking strategy (tokens, recursive, etc.)
