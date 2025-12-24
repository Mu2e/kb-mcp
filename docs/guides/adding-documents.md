# Adding New Documents

Three ways to add documents to your knowledge base:

- [Manual Upload via CLI](#1-quick-start-manual-upload)
- [Using Importers (e.g., INSPIRE-HEP)](#2-using-importers)
- [Programmatic Upload (Python API)](#3-programmatic-upload-advanced)


## 1. Quick Start: Manual Upload

### Single File

```bash
# Ingest a document
kb ingest my-paper.pdf --source-id papers --doc-id paper-001

# Chunk and embed it for search
kb embed papers_paper-001
```

### Batch Upload

```bash
# Ingest all PDFs from a directory
for file in docs/*.pdf; do
  kb ingest "$file" --source-id archive
done

# Chunk and embed all documents that don't have chunks yet
kb tools chunk-and-embed-all archive

# Or if documents are already chunked, just embed chunks without embeddings
kb embedding embed-all --source-id archive
```

## 2. Using Importers

The importers download the data and extract the text, by default chunk the documents and embeds them. 

### INSPIRE-HEP Physics Papers

```bash
# Import papers by collaboration
kb-import inspire \
  --query "collaboration:SLD" \
  --max-results 100 \
  --source-id inspire-sld
```

Documents are automatically chunked and embedded (use `--no-auto-embed` to skip).

## 3. Programmatic API

```python
from kb_mcp.kb import add_from_path

# Add a single document
doc = add_from_path(
    "paper.pdf",
    source_id="papers",
    doc_id="paper-001",
    auto_embed=True  # Automatically chunk and embed
)

# Add with custom metadata
doc = add_from_path(
    "report.pdf",
    source_id="reports",
    metadata={
        "experiment": "Mu2e",
        "category": "detector"
    },
    auto_embed=True
)
```
