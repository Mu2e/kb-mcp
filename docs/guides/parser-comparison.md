# Parser Comparison Guide

When the same raw document is parsed by multiple parsers (e.g. `marker`, `docling`, `nougat`, `azure`), the parser comparison workflow lets you systematically evaluate which parser produces the best output — and derive actionable guidance for your corpus.

The workflow has two passes:

- **Pass 1** — per-document: an LLM reads all parsed versions of a single raw document and writes a free-text comparison, plus a short description of the document's nature.
- **Pass 2** — source-level: an LLM reads all pass-1 comparisons for a source and synthesizes categories and recommendations (e.g. "parser X is best for scanned text with equations").

Results are stored in the database and visible in the web interface at `/web/raw/<raw_document_id>`.

---

## Prerequisites

You must have parsed the same raw documents with at least two different parsers before running comparisons. See the [Adding Documents guide](adding-documents.md) and the `tools parse-all` command.

```bash
# Example: parse with marker and docling
kb tools parse-all sld-scanned --parser-name marker
kb tools parse-all sld-scanned --parser-name docling
```

---

## Pass 1: Per-Document Comparison

### Single document

```bash
kb compare run --raw-document-id <uuid>
```

### Entire source (batch)

```bash
kb compare run --source-id sld-scanned
```

Already-compared documents are skipped automatically. Use `--force` to re-run everything:

```bash
kb compare run --source-id sld-scanned --force
```

### Options

| Flag | Description |
|---|---|
| `--parsers marker docling` | Restrict to specific parsers (default: all available) |
| `--model <name>` | Override the LLM (default: `PARSER_COMP_MODEL` env var) |
| `--force` | Re-run even if a comparison already exists |
| `--limit N` | Stop after N raw documents (useful for testing) |
| `--workers N` | Parallel LLM calls (default: 1) |

Each comparison stores:
- A short **document description** (type, subject, structural characteristics such as equations, tables, scanned text)
- A free-text **analysis** comparing completeness, accuracy, structure preservation, and overall quality

---

## Pass 2: Source-Level Categorization

Once you have pass-1 comparisons for a source, run pass-2 to synthesize categories:

```bash
kb compare categorize --source-id sld-scanned
```

This creates a new `ParserCategories` record — previous runs are never overwritten, so you build up a history.

### Focused analyses

Use `--prompt-extra` to ask the LLM to focus on a specific dimension:

```bash
kb compare categorize --source-id sld-scanned \
  --prompt-extra "Focus especially on equation and LaTeX notation handling"

kb compare categorize --source-id sld-scanned \
  --prompt-extra "Focus on how each parser handles scanned pages and OCR quality"
```

Each focused run is stored separately and visible in the categories history.

### List categorization runs

```bash
kb compare categories-list --source-id sld-scanned
```

---

## Inspecting Results

### CLI

```bash
# List all per-document comparisons for a source
kb compare list --source-id sld-scanned

# Show the comparison for a specific raw document
kb compare get <raw-document-uuid>
```

### Web interface

Navigate to any document page (`/web/document/<id>`) and click the **Raw Document ID** link. The raw document page (`/web/raw/<id>`) shows:

1. **Raw Document** — file metadata and all parsed versions (linked to their document pages)
2. **Parser Comparison** — the per-document LLM analysis and document description
3. **Parser Recommendations** — the most recent source-level categorization

---

## Export for Frontier-Model Analysis

Export all comparisons as a single markdown document to paste into Claude, GPT-4, or another frontier-model chat interface:

```bash
kb compare export --source-id sld-scanned --output sld_comparisons.md
```

Without `--output`, the markdown is printed to stdout (useful for piping).

The export includes one section per document with its description and comparison analysis. You can then ask the frontier model open-ended questions about the corpus, combine it with additional context, or use it as input for a more detailed manual review.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PARSER_COMP_MODEL` | LLM used for pass-1 and pass-2 | `DEFAULT_LLM_MODEL` |
| `DEFAULT_LLM_MODEL` | Fallback model for all LLM tasks | `gemini-2.5-flash-lite` |

---

## Typical Workflow

```bash
# 1. Parse the source with multiple parsers
kb tools parse-all sld-scanned --parser-name marker
kb tools parse-all sld-scanned --parser-name docling
kb tools parse-all sld-scanned --parser-name nougat
kb tools parse-all sld-scanned --parser-name azure

# 2. Run pass-1 comparisons (can be interrupted and resumed safely)
kb compare run --source-id sld-scanned

# 3. Run pass-2 broad synthesis
kb compare categorize --source-id sld-scanned

# 4. Run targeted analyses for dimensions you care about
kb compare categorize --source-id sld-scanned \
  --prompt-extra "Focus on equation and LaTeX handling"

# 5. Export for ad-hoc frontier-model analysis
kb compare export --source-id sld-scanned --output comparisons.md

# 6. Browse results in the web interface
#    /web/raw/<raw_document_id>  (linked from any document page)
```
