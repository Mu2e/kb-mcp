# Document Operations

Functions for adding, retrieving, and managing documents.

## High-Level Ingestion

::: kb_mcp.kb.ingest

The main entry point for adding documents with full processing (parse → summarize → chunk/embed).

::: kb_mcp.kb.add_document

Lower-level function for parsing and adding documents without automatic summarization or embedding.

## Retrieve Documents

::: kb_mcp.kb.get

::: kb_mcp.kb.get_count

## Delete Documents

::: kb_mcp.kb.delete_document

## Document Class Methods

The `Document` class provides convenience methods for common operations on document instances.

### Chunking and Embedding

::: kb_mcp.kb.db_models.Document.chunk

::: kb_mcp.kb.db_models.Document.get_chunks

::: kb_mcp.kb.db_models.Document.drop_chunks

::: kb_mcp.kb.db_models.Document.chunk_and_embed

### Summary Generation

::: kb_mcp.kb.db_models.Document.generate_summary

### Creation Methods

::: kb_mcp.kb.db_models.Document.from_dict

::: kb_mcp.kb.db_models.Document.from_file

::: kb_mcp.kb.db_models.Document.to_dict
