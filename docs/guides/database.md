# KB Database Structure

This KB is built around a SQL database schema. It is intended for `postgresql` but `sqlite` can be used for development work.

The core table is [Document](#kb_mcp.kb.db_models.Document) that holds all documents of the KB, with [Source](#kb_mcp.kb.db_models.Source) holding information about the document's origin. For the semantic search we store document chunks in [Chunk](#kb_mcp.kb.embedding.db_models.Chunk) with embeddings stored in `embeddings_NAME`. 

Details of document parsing, chunking, and embedding are logged in [logs_XX](#logging) tables. The kb evaluation ([details](evaluation.md)) schema uses [eval_XXX](#evaluation) tables.


## Documents and Sources

::: kb_mcp.kb.db_models.Document
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.db_models.Source
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

## Semantic Search
Chunks, potentially with different strategies, of the main documents are stored in a [chunks](#kb_mcp.kb.embedding.db_models.Chunk) table. Metadata of the different chunking strategies are stored in [chunk_strategies](#kb_mcp.kb.embedding.db_models.ChunkStrategy). The chunks are embedded, potentially with multiple embeddings. Each embedding is stored in an independent `embeddings_NAME` table because the dimensions of the fields depend on the embedding dimensions. Embedding configurations are stored in [embedding_configs](#kb_mcp.kb.embedding.db_models.EmbeddingConfig)

::: kb_mcp.kb.embedding.db_models.Chunk
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []


::: kb_mcp.kb.embedding.db_models.ChunkStrategy
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

### Embedding 
| Name | Type | Description |
| :--- | :--- | :--- |
| `id` | `str` | Primary key (UUID stored as string). |
| `chunk_id` | `str` | Foreign key to the chunks table. |
| `embedding` | `vector` | Embedding vector. |
| `created_time` | `datetime` | Timestamp when the embedding was created. |


::: kb_mcp.kb.embedding.db_models.EmbeddingConfig
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

## Logging
Document operations are logged with a focus on timing information. Document parsing is logged in [logs_parsing](#kb_mcp.kb.embedding.db_models.ParsingLog), chunking and embedding in [logs_chunking_embedding](#kb_mcp.kb.embedding.db_models.ChunkEmbeddingLog). In addition, all searches (aka vector lookups) are logged in [logs_search](#kb_mcp.kb.search.db_models.SearchLog).

::: kb_mcp.kb.embedding.db_models.ParsingLog
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.embedding.db_models.ChunkEmbeddingLog
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.search.db_models.SearchLog
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []


## Evaluation
The evaluation schema is described in more detail at [evaluation](evaluation.md). The eval datasets are stored in [eval_dataset](#kb_mcp.kb.eval.db_models.EvalDataset), with their generation metadata in [eval_generation](#kb_mcp.kb.eval.db_models.EvalGeneration) and optional audit/review filtering in [eval_audit](#kb_mcp.kb.eval.db_models.EvalAudit). Evaluation runs/experiments are stored in [eval_run](#kb_mcp.kb.eval.db_models.EvalRun) with their results in [eval_results](#kb_mcp.kb.eval.db_models.EvalResult) and additional, per result per document logging in [eval_retrieved_documents](#kb_mcp.kb.eval.db_models.EvalRetrievedDocument).


::: kb_mcp.kb.eval.db_models.EvalDataset
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.eval.db_models.EvalGeneration
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.eval.db_models.EvalAudit
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.eval.db_models.EvalRun
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.eval.db_models.EvalResult
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []

::: kb_mcp.kb.eval.db_models.EvalRetrievedDocument
    options:
      show_root_heading: true
      show_root_full_path: false
      heading_level: 3
      show_bases: false
      members: []
