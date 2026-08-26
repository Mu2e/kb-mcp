# Handoff: nested-tree traversal for HTML in the section walker

Written 2026-08-26. Load this at the start of the next session.

## The task

`chunk_from_docling_json()` walks a DoclingDocument body to cut section-aware
chunks. It handles a **flat** body: it iterates `body.children`, processes
`#/texts/*`, and descends **one** level into `#/groups/*`.

Docling's **HTML** reader emits a **nested** tree instead — texts own groups,
groups own texts, recursively. So for HTML the walker sees almost nothing.

Fix the traversal so nested HTML documents get real section structure, without
regressing the flat PDF path.

## Evidence (reproduce before changing anything)

Two documents already in the DB, deliberately contrasting:

| id | what | body shape | status |
|---|---|---|---|
| `a18718c2-7988-4301-8fd6-826204a0d5f0` | mu2e-wiki / Mu2e_Offline_Tutorial | **nested** (HTML) | the bug |
| `e7a4fc55-4d41-4ba6-a723-3c752ad480ec` | mu2e-docdb / 57799-IFAE_2026_Proceedings | flat (PDF) | regression guard |

For the wiki document, its persisted `parser_output` has **47 text elements**, but:

- `body.children` lists only `#/texts/0` and `#/texts/1`
- 11 groups hang off *text* nodes (`#/texts/14`, `#/texts/20`, `#/texts/2`, …)
- texts hang off those groups (`#/groups/0` has 6 children, `#/groups/6` has 5, …)
- labels present: `title` 2, `section_header` 7, `list_item` 15, `text` 23

The walker locates **2 of 47** elements. Verify with:

```python
from kb_mcp.kb.database import get_db_session
from kb_mcp.kb.db_models import Document
from kb_mcp.kb.embedding import chunking as ch

with get_db_session() as s:
    d = s.query(Document).filter(Document.id == 'a18718c2-7988-4301-8fd6-826204a0d5f0').first()
    po, calls = d.parser_output, []
    orig = ch._find_loose
    ch._find_loose = lambda h, n, st: (calls.append(n[:30]), orig(h, n, st))[1]
    cs = ch.chunk_from_docling_json(d, parser_output=po, prepend_section_path=True)
    ch._find_loose = orig
print(len(calls), "locate attempts ->", len(cs), "chunks")
print({c.get("section_path") for c in cs})
```

**Current output:** 2 locate attempts, 3 chunks, all with
`section_path == "Mu2e Offline Tutorial"` (the document title). Chunk *sizes*
are fine — that was a separate bug, fixed 2026-08-26 (see below). What is
missing is section structure: 7 `section_header` elements produce no
subsection paths.

**Target:** most of the 47 elements located, `section_path` reflecting the
real heading hierarchy, chunk sizes still near the budget.

## Where the code is

`src/kb_mcp/kb/embedding/chunking.py` (line numbers as of this handoff):

| line | what |
|---|---|
| 400 | `chunk_from_docling_json()` — the walker |
| 512 | `children = body.get("children")` — **the traversal to change** |
| 517 | `groups_by_ref` — group lookup table |
| 890 | `elif cref.startswith("#/groups/")` — the one-level-deep descent |
| 718 | `locate()` — anchors element text in the exported Markdown |
| 742 | `add_text()` — accumulates a located element into the current chunk |
| 903 | `if not located_any:` — bail-out that falls back to the token chunker |
| 19-20 | `_MIN_ANCHOR_LEN = 24`, `_MAX_SHORT_JUMP = 400` |

## Traps

1. **Don't regress PDFs.** The flat path works today. `e7a4fc55-…` is the guard:
   currently 12 chunks, avg 1353 chars, max 2155. Re-measure after the change.

2. **`locate()` will see far more short strings.** Recursing surfaces list items
   and short headings. `_MIN_ANCHOR_LEN`/`_MAX_SHORT_JUMP` exist precisely because
   a short needle can match spurious text far ahead and drag the cursor forward,
   making every later element unfindable. Expect to tune these, and check the
   cursor stays monotonic.

3. **Cycles.** Parent/child refs point both ways (`texts[i].parent` →
   `#/groups/j`, and groups list texts as children). A naive recursion can loop —
   track visited `self_ref`s.

4. **Reading order must be preserved.** Chunks are contiguous slices of
   `document.text`; `char_start_index`/`char_end_index` must remain exact slices.
   `tests/unit/test_chunking_docling.py::test_chunk_text_is_an_exact_slice_of_document_text`
   pins this.

5. **`section_path` is read at flush time** while `header_stack` still describes
   the closing section — see the comment at the `section_header` branch. Nested
   traversal must push/pop the stack in the right order or paths go wrong silently.

## Verification plan

Chunk counts alone are not enough; check `section_path` correctness.

1. Before/after on both documents above via the snippet.
2. Widen to a spread of types — there are PPTX, DOCX, XLSX and more HTML in the
   corpus. `kb reparse --from-raw --source-id mu2e-wiki --dry-run` lists candidates.
3. `pytest tests/unit -q` — **261 passing** at handoff.
4. Re-chunking is needed for any document to benefit; chunks in the DB today are
   still the old fragmented ones under the old `section` name.

## Already fixed 2026-08-26 — do not redo

All uncommitted.

- **Paragraph packing** (`_split_to_budget`, line 276): paragraphs under the cap
  were each emitted as their own chunk; the sentence branch packed but the
  paragraph branch did not. Wiki 24 chunks (avg 167 ch) → 3 (avg 1350); PDF 25
  (avg 649) → 12 (avg 1353). Merged slices are re-measured, not summed, because
  merging re-includes the `\n\n` that `cut` drops.
- **`section_512`** (`resolve_strategy_name`, line 246): `section` is now
  window-encoded like `summary`, so a re-chunk under a different encoder no
  longer silently replaces the old set.
- **`self.mime_type` → `self.doc_type`** (`parser_docling.py`): the formula
  auto-enrichment branch read a non-existent attribute; the `AttributeError` was
  swallowed by a debug-level `except`, so `PARSE_FORMULA_ENRICHMENT_AUTO` had
  **never** fired for any document, and it masked the manual flag too. That
  `except` now logs at warning.
- **Undecoded-formula fallback** (`_fill_undecoded_formulas`): an undecoded
  formula keeps its raw reading in `orig` while `text` is empty, and the Markdown
  export only reads `text`. Placeholders now fall back to `orig`.
- **Embedding model → `BAAI/bge-small-en-v1.5`** (384 dims, 512-token window) in
  `.env`, `.env.example`, and `DEFAULT_MODELS` in `kb/embedding/utils.py`.
  Table `embeddings_st_bgesmallenv1_5`. Retrieval stays CPU-only (~50-100 ms/query
  on 4 cores); bulk indexing ~4.2 h CPU, worth a GPU.
- **Short-name mangling** (`_generate_short_name`): `.replace("all-", "")` was
  unanchored and fired inside "bge-sm**all-**en-v1.5". Now `^all-`.
- **`kb reparse`** — three modes: default (raw file), `--from-stored` (reuse the
  stored parse tree), `--from-raw` (drive from `documents_raw`; orphans only
  unless `--force-reparse`). Replaced `kb retext` and `scripts/reparse_empty_docs.py`.
- **`_update_document` carries `parser_output`** — without it a re-parse dropped
  the DoclingDocument and silently downgraded chunking to token windows.
- **Parser naming**: `kb-mcp`/`auto` resolves to the real backend (`docling`)
  before it is recorded in `documents.parser_id`.

## Other open items

1. **Dedup creates duplicate rows on re-parse.** Identity is
   `(source_id, doc_id, parser_id)` (`kb/documents/operations.py:60-71`). Because
   `parser_id` now resolves `kb-mcp` → `docling`, re-parsing an existing document
   **inserts a new row** instead of updating. Four rows to clean from testing:
   keep `e7a4fc55-…` and `a18718c2-…`, drop the empty `bf8c39cd-93d4-4661-a002-8c1edf3b014b`
   and `ce365430-2265-44a4-b4f9-feb1ace72548`. **Fix this before any full reprocess** —
   it would duplicate the whole corpus.
2. **~5749 documents hold both `tokens_1000_200` and `section` chunks**, so search
   returns the same passage twice. A targeted SQL delete was offered, not approved.
3. **Nothing is committed.** 76 changed/untracked paths in the tree, most of them
   other people's work. Only `health.py`, `kb/embedding/utils.py` and the new
   `tests/unit/test_{embedding_short_name,formula_fallback,formula_enrichment_dispatch,parser_name_resolution,update_document_parser_output,image_description_preflight}.py`
   are exclusively mine. Everything else needs **hunk-level** staging — never
   `git add <file>` on the shared ones, never `git commit -a`.
4. **`kb reparse` configures no logging**, so INFO lines are invisible. That is
   what made the formula bug take three runs to find. Worth a `--verbose`.
5. **Non-Docling parser names**: markdown/plain-text documents still record the
   `kb-mcp` sentinel; naming `TextParser`/`ExcelParser`/legacy-PPTX needs a decision.
6. **44 orphan raw files** (`kb reparse --from-raw --dry-run`): 25 would create new
   documents, 19 would update existing ones. About half live under
   `/exp/mu2e/app/users/wzhou2/…`.

## Environment

```bash
source scripts/setup_mu2e_uv.sh        # must be sourced
# non-interactive: /tmp/$USER/kb-env-uv/bin/python

PG=$(find /cvmfs/mu2e.opensciencegrid.org/packages/postgresql -maxdepth 4 \
     -path "*almalinux9*" -name psql -printf '%h\n' | head -1)
set -a && source .env && set +a
PGPASSWORD="$DB_PASSWORD" "$PG/psql" -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
```

This node has **4 CPUs and no CUDA**; benchmark latencies here are noisy under
load — trust ratios, not absolute numbers.
