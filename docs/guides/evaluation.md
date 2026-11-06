# Evaluation

The evaluation module measures how well the knowledge base answers questions: it
generates questions from documents, filters them, answers them with one or more models
in different modes, and scores the answers with an LLM judge. See
[database schema](database.md#evaluation) for how evaluation data is stored.

**Workflow:**

1. **[Generate](#1-generate-questions)** questions from documents
2. **[Audit](#2-audit-questions)** them (automatic overlap check, LLM audit, human review)
3. **[Run](#3-run-evaluations)** evaluations -- usually as a [grid](#grid) over models, modes and judges
4. **[Compare](#4-compare-and-report)** runs, list [source conflicts](#source-conflicts), build a report

## Models and endpoints

Every LLM step takes a model name (`--model`, `--judge-model`, `--answer-model`) or
falls back to its setting: `EVAL_GEN_MODEL`, `EVAL_AUDIT_MODEL` (default: the judge
model, so a generator never vets its own questions), `EVAL_JUDGE_MODEL`, and
`EVAL_ANSWER_MODEL` (default `DEFAULT_LLM_MODEL`). Models are routed to endpoints with
`OPENAI_BASE_URL_MODELS` / `OPENAI_API_KEY_MODELS`; keys may be a prefix such as
`"argo:*"` to route a whole gateway. For ANL's Argo gateway, `scripts/argo-tunnel.sh start`
keeps `argo-proxy` and an ssh forward to it running.

Gateway refusals are never taken as answers: a reply carrying Argo's over-limit notice
raises an error that stops the run, which can then be resumed (see [grid](#grid)).

## 1. Generate questions

```bash
# From explicit documents (one documents.id per line, '#' comments allowed)
kb eval generate --name my-gen --model gpt-oss:120b --strategy keypoint \
    --num-questions 2 --doc-ids-file docs.txt

# A second generator on exactly the same documents, for comparison
kb eval generate --name my-gen-gpt55 --model argo:gpt-5.5 --strategy keypoint \
    --num-questions 2 --same-documents-as <generation_id>

# Random documents from a source
kb eval generate --source-id mu2e-docdb --num-documents 10
```

The keypoint prompt shares its quality criteria with the auditor: questions must be
self-contained, externally motivated, answerable, well-formed, and **not worded from the
document**. Each question stores a reference answer and its wording overlap with the
source; `kb eval overlap --generation-id <id>` reports overlap per question set. A
generation's identity includes its model and name, so two generators never merge.

## 2. Audit questions

```bash
# Automatic: reject questions sharing > 40% of word pairs with their source
kb eval audit --generation-id <id> --overlap-max 0.4 --limit 0

# LLM audit against the shared criteria (EVAL_AUDIT_MODEL unless --model)
kb eval audit --generation-id <id> --llm --model argo:claude-opus-5 --limit 0 --workers 6

# Interactive human review
kb eval audit --generation-id <id>
```

Runs use **strict selection** by default in the grid (`--strict-audit` for `kb eval run`):
a question is used only if it has a valid audit and no invalid one; a human review, if
present, overrides the automatic audits.

## 3. Run evaluations

### Answer modes

| `--search-type` | What answers | Notes |
|---|---|---|
| `semantic`, `fulltext`, `hybrid` | -- (retrieval only) | hit = source document retrieved |
| `llm_only` | the model alone | no knowledge base |
| `rag` | one real `kb_search`, then one answer | what a client's first search achieves |
| `agentic` | the model with the real MCP tools | `kb_search` and `kb_get`, server instructions, `research_question` prompt |

Agentic runs call the MCP tools in-process (same tool definitions and results as a
connected client, without transport/auth). Limits: `EVAL_AGENTIC_MAX_TURNS` (10) and
`EVAL_AGENTIC_TOOL_RESULT_MAX_CHARS` (100000), overridable per run with `--max-turns` and
`--tool-result-max-chars`. An empty reply (no text, no tool call) gets a nudge rather than
being taken as the answer; nudges are recorded in the trace.

Retrieval is scored at two levels: **exact** (the source row) and **entry** (any file
version or figure of the source's DocDB entry).

### Grid

```bash
kb eval grid --prefix my-eval \
    --generation-id <gen1> --generation-id <gen2> \
    --search-type agentic --search-type rag --search-type llm_only \
    --answer-model gpt-oss:120b --answer-model argo:claude-sonnet-5 \
    --judge-model argo:gpt-5.5 --judge-model argo:claude-opus-5 \
    --workers 4            # add --dry-run to see the plan first
```

Runs are named `<prefix>-<mode>-<answer model>-q<question set>-judge-<judge>[-v<N>]`.
The grid is **resumable**: complete runs are skipped, and an interrupted run is
continued in place (its answers are kept, only missing questions are answered). With
several judges, answers are produced once and **re-judged** by the others. A newer judge
prompt version re-judges existing answer runs instead of answering again.

Single runs: `kb eval run ...`; re-judge one run: `kb eval rejudge --run-id <id> --judge-model <model>`.

### Judge

Answer-mode verdicts use judge prompt version 3 (recorded in each run's
`judge_strategy` and name):

- Scoring is **strict against the reference answer**; extra correct detail is not
  penalised. An answer giving both the reference value and a conflicting one, and noting
  the disagreement, counts as correct.
- Each verdict also records, in the result's `meta`, a **source relation**
  (`matches_reference`, or a differing value attributed to a `newer_source`,
  `older_source` or `conflicting_source` document), whether the answer acknowledges the
  conflict, and the DocDB numbers the differing value is attributed to -- verified in
  code (exists? newer or older than the question's source?).
- A judge failure is stored as *not judged*, never as wrong; such runs count as
  incomplete and are redone by the grid.

Version 2 accepted values from newer documents as correct and proved gameable; do not
compare v2 accuracy with other versions.

## 4. Compare and report

```bash
kb eval compare --prefix my-eval                 # one row per run
kb eval compare --prefix my-eval --per-question  # question x run verdict matrix
kb logs tokens --stage eval_answer               # token use (tagged per run and question)
```

The web UI's **Compare runs** page (`/web/eval/compare?prefix=my-eval`) shows the same
table and a per-question matrix linking to each answer and conversation.

### Source conflicts

```bash
kb eval conflicts --prefix my-eval [--json]
```

Lists questions whose answers differ from the reference because of another document,
most corroborated first (several models citing the same existing newer document). These
are candidates for an outdated reference -- input for human review, not verdicts.

### Report

`analysis/eval_report.py` (needs the `analysis` extra) writes a grid's numbers as LaTeX
macros (`numbers.tex`), a `data.json`, and figures, for an internal note or slides:

```bash
python analysis/eval_report.py --prefix my-eval- --questions <gen1> --questions <gen2> --out <dir>
```

## Caveats

- **Claude token counts through Argo are lower bounds**: argo-proxy's OpenAI-format usage
  omits prompt-cache writes.
- DocDB reports the same quantity differently over time, so reference answers age; see
  [source conflicts](#source-conflicts).
