# Extending kb-mcp

This guide explains how to add additional different chunking strategies and embedding strategies.

## Using Chunking Strategies

To add a new chunkling in addition to the baseline:

### CLI

```bash
# Use summary strategy (creates chunks from document summaries)
kb tools chunk-and-embed-all inspire-sld --strategy summary

# Use tokens strategy without gist prepending
kb tools chunk-and-embed-all inspire-sld --strategy tokens --no-gist

# Use tokens strategy without section path prepending
kb tools chunk-and-embed-all inspire-sld --strategy tokens --no-section-path

# Use tokens strategy without both gist and section path
kb tools chunk-and-embed-all inspire-sld --strategy tokens --no-gist --no-section-path
```

### Python

```python
from kb_mcp.kb.tools import chunk_and_embed_all

# Use summary strategy (creates chunks from document summaries)
chunk_and_embed_all(source_id="inspire-sld", chunk_strategy="summary")

# Use tokens strategy without gist prepending
chunk_and_embed_all(
    source_id="inspire-sld",
    chunk_strategy="tokens",
    chunk_config={"prepend_gist": False}
)

# Use tokens strategy without section path prepending
chunk_and_embed_all(
    source_id="inspire-sld",
    chunk_strategy="tokens",
    chunk_config={"prepend_section_path": False}
)

# Use tokens strategy without both gist and section path
chunk_and_embed_all(
    source_id="inspire-sld",
    chunk_strategy="tokens",
    chunk_config={"prepend_gist": False, "prepend_section_path": False}
)
```



## Adding a New Embeddings 

TODO: 