# Parser

Document parsing utilities for extracting text and metadata from various formats (PDF, DOCX, PPTX, Excel, etc.).

**Standalone usage:** This module can be used independently of the knowledge base.

**Key features:**
- Extracts text from PDF, DOCX, PPTX, Excel, and other formats
- Handles image extraction from documents (e.g., figures from PDFs)
- CLI tool available: `kb-parse` for command-line parsing

## PDF parser selection

The PDF dispatch in `parse()` accepts an optional `parser_name`:

| `parser_name` | Backend | Notes |
|---|---|---|
| `None` / `"kb-mcp"` (default) / `"docling"` | IBM Docling (`parser_docling.py`) | DocLayNet layout model + TableFormer; structure-preserving Markdown with headings, tables, bullets, hyphenated identifiers, figure placeholders. Auto-uses CUDA via `AcceleratorOptions` when `torch.cuda.is_available()`; falls back to CPU. Requires the `[docling]` optional dependency group. |
| `"marker"` | `marker-pdf` (`parser_marker.py`) | Strongest table extractor on dense technical reports but ≈ 6× slower than Docling on GPU. Requires the `[marker]` optional dependency group. |
| `"pypdf2"` | PyPDF2 (`parser_pdf.py`) | Legacy fast path. ≈ 45× faster than Docling but loses headings, tables, list structure, and hyphenated identifiers. Use for benchmarking or as an opt-out on CPU-only hosts. |

The relative quality/throughput trade-offs in the table above were measured on a 12-PDF Mu2e DocDB sample spanning meeting summaries, small/medium/large slide decks, and a single-page table.

PPTX, HTML/XHTML, and DOCX also route through Docling by default (`parser_name="legacy"` opts back into the bespoke parser for any flipped format). XLSX, plain text, and generic XML keep their bespoke parsers via `get_parser()` and `PARSER_MAP` — Docling emits one table record per spreadsheet row, the wrong granularity for retrieval.

## Persisted structured parser output

The `documents.parser_output` column (`JSONB` on PostgreSQL, `JSON` on SQLite) stores the raw structured output of whichever parser produced the document. It is deliberately parser-agnostic: any parser can expose a `structured_output` attribute and `parse()` will persist it, so alternative parsers can be tested side by side in the same infrastructure. Payloads should self-identify their schema — DoclingDocument dumps carry an embedded `schema_name` field, and downstream readers guard on it via `is_docling_document()` (defined in `kb/db_models.py`, re-exported by `kb/parser_views.py`) before walking the structure.

For the Docling path, `parse()` attaches the full structured `DoclingDocument` (as a JSON-serialisable dict via `model_dump(mode="json")`) to `doc_data["parser_output"]`, and `Document.from_dict()` carries it through to the column. Embedded picture bytes are stripped before persistence — they account for ≈ 95 % of the dict size and are already extracted as separate `doc_type="image"` `Document` records. The retained structure (texts, tables, pictures-metadata, pages, bboxes, refs) is the source-of-truth representation that lets future retrieval views (table-as-record indices, contextual embedding, hierarchical retrieval, ColPali visual retrieval) be re-derived without re-parsing the original PDF.

## Table records (`doc_type="table"`)

For each table the layout model finds, `DoclingParser` emits a sibling Document with:

- `doc_type="table"`, `source_type="text/markdown"`
- `text` = caption (if any) + Markdown-rendered cell grid
- `meta = {table_index, page, bbox, caption, num_rows, num_cols, self_ref, parser, …}`
- `parent_id` set to the parent text Document after both rows are saved

`parse()` returns these alongside the main text Document and the image Documents. The existing add → chunk → embed pipeline indexes them like any other Document; the chunker's contextual prefix uses the parent doc's gist when available. Search code can filter or boost on `doc_type="table"`; the query router uses this to lift table records for table-flavoured queries.

## Main Parser Function

::: kb_mcp.parser.parse

## Utilities

::: kb_mcp.parser.detect_mime_type

::: kb_mcp.parser.display_image
