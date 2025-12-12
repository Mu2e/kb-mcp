# KB Module API

The `kb` module provides the core knowledge base functionality for document storage, chunking, embedding, and semantic search.

## Overview

The KB module is organized into several sub-modules:

- **kb** - Core document storage and retrieval (functions listed below)
- **[kb.embedding](kb/embedding.md)** - Chunking and embedding operations (see also [batch operations](kb/batch.md))
- **[kb.search](kb/search.md)** - Semantic search functionality
- **[kb.eval](kb/evaluation.md)** - Evaluation and benchmarking (see also [evaluation guide](../guides/evaluation.md))

See [database schema](../guides/database.md) for details on data models and storage.

## Quick Start

```python
from kb_mcp.kb import get, search

# Get a document
doc = get("papers_paper-001")
print(doc.title)

# Search
results = search("quantum mechanics", max_results=5)
for result in results:
    print(f"{result['document'].title}: {result['score']}")
```

## All Available Functions

**[Search](kb/search.md):** [`search`](kb/search.md#search)

**[Document Operations](kb/documents.md):** [`add`](kb/documents.md#add), [`add_from_path`](kb/documents.md#add_from_path), [`add_many`](kb/documents.md#add_many), [`get`](kb/documents.md#get), [`get_count`](kb/documents.md#get_count), [`get_options`](kb/documents.md#get_options), [`get_children`](kb/documents.md#get_children), [`delete_document`](kb/documents.md#delete_document)

**[Source Management](kb/sources.md):** [`add_source`](kb/sources.md#add_source), [`list_sources`](kb/sources.md#list_sources)

**[Utilities](kb/utilities.md):** [`deduplicate`](kb/utilities.md#deduplicate), [`find_all_duplicates`](kb/utilities.md#find_all_duplicates), [`get_stats`](kb/utilities.md#get_stats), [`get_statistics`](kb/utilities.md#get_statistics), [`get_metadata_keys`](kb/utilities.md#get_metadata_keys)

**[Batch Operations](kb/batch.md):** [`chunk_and_embed_all`](kb/batch.md#chunk_and_embed_all), [`image_chunk_and_embed_all`](kb/batch.md#image_chunk_and_embed_all)

**[Logging](kb/logging.md):** [`get_search_logs`](kb/logging.md#get_search_logs), [`get_parsing_logs`](kb/logging.md#get_parsing_logs), [`get_chunking_logs`](kb/logging.md#get_chunking_logs), [`get_all_logs_for_document`](kb/logging.md#get_all_logs_for_document)

**[Embedding](kb/embedding.md):** [`chunk_document`](kb/embedding.md#chunk_document), [`get_chunk_strategies`](kb/embedding.md#get_chunk_strategies), [`get_chunks`](kb/embedding.md#get_chunks), [`embed_chunk`](kb/embedding.md#embed_chunk), [`embed_chunks`](kb/embedding.md#embed_chunks), [`chunk_and_embed`](kb/embedding.md#chunk_and_embed), [`get_embeddings`](kb/embedding.md#get_embeddings), [`get_embedding_vector`](kb/embedding.md#get_embedding_vector)

**[Evaluation](kb/evaluation.md):** [`generate_questions_from_documents`](kb/evaluation.md#generate_questions_from_documents), [`generate_questions_from_source`](kb/evaluation.md#generate_questions_from_source), [`audit_question`](kb/evaluation.md#audit_question), [`get_unaudited_questions`](kb/evaluation.md#get_unaudited_questions), [`eval`](kb/evaluation.md#eval), [`get_summary_stats`](kb/evaluation.md#get_summary_stats)

**[Database](kb/database.md):** [`init_db`](kb/database.md#init_db), [`get_db_session`](kb/database.md#get_db_session)


