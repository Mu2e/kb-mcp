# Search

Search functionality combining semantic (vector) and full-text search for finding relevant documents.

## Search Types

The module provides three search functions:

- **`search()`** - Hybrid search (recommended) that combines semantic and full-text search using Reciprocal Rank Fusion (RRF)
- **`search_semantic()`** - Semantic-only search using vector embeddings for conceptual similarity
- **`search_fulltext()`** - Full-text-only search using PostgreSQL tsvector for keyword matching
- **`search_hybrid()`** - Explicit hybrid search function (same as `search()`)

## Configuration

Search behavior can be configured via environment variables (see [configuration](../config.md)):

- `SEARCH_MAX_CHUNKS_PER_DOC` - Maximum chunks per document in results (default: 10)
- `SEARCH_INITIAL_LIMIT_MULTIPLIER` - Initial retrieval multiplier before deduplication (default: 50)
- `SEARCH_RRF_K` - Reciprocal Rank Fusion constant for hybrid search (default: 60)

## API Reference

::: kb_mcp.kb.search

::: kb_mcp.kb.search_semantic

::: kb_mcp.kb.search_fulltext