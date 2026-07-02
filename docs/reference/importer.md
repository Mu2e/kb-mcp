# Importer

Document importer utilities for fetching and processing documents from external sources.

The imports module provides a base `Source` class for creating custom importers that fetch documents from external APIs or file systems and add them to the knowledge base.

**Available importers:**

- **DocDB**: Multi-entity importer for Mu2e DocDB. Supports documents (XML-first parsing with HTML fallback), events, topics, authors, keywords, groups, and doc-types. Documents create searchable text; events create both Documents and Graph nodes; other entity types create Graph nodes only (structural metadata). Authenticates via Kerberos or PingFederate SSO.
- **MediaWiki**: Imports pages from MediaWiki sites (e.g., Mu2e Wiki). Kerberos auth supported.
- **INSPIRE-HEP**: Imports physics papers from the INSPIRE-HEP database via REST API.
- **Code**: Imports source code from local git repositories. Parses C++, FHiCL, and Python files with tree-sitter AST parsing. Extracts art module types, FHiCL parameters, and includes.

**CLI usage:** `kb-import <source> [options]`

```bash
# DocDB entity types
kb-import docdb --type topics --skip-existing -v
kb-import docdb --type authors --skip-existing -v
kb-import docdb --type documents -q "recent:365" --skip-existing -v
kb-import docdb --type all -v

# Other sources
kb-import wiki --query all --skip-existing -v
kb-import inspire --query "find exp mu2e" --max-results 30
kb-import code --path /path/to/Offline --repo-name Offline --skip-existing
```

## Base Class

::: kb_mcp.imports.Source

## DocDB Source

::: kb_mcp.imports.docdb.DocDBSource
