# Importer

Document importer utilities for fetching and processing documents from external sources.

The imports module provides a base `Source` class for creating custom importers that fetch documents from external APIs or file systems and add them to the knowledge base.

**Example importers:**

- InspireHEP: Imports physics papers from the INSPIRE-HEP database
- Custom sources: Extend the `Source` base class to create your own importers

**Standalone usage:** This module can be used independently or via the CLI tool `kb-import` for command-line importing from external sources.

::: test_mcp.imports.Source
