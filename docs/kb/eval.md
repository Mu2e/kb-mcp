# Evaluation Module - Quick Start

## Generate Evaluation Dataset

### CLI

Generate questions from documents using the `keypoint` method (default):

```bash
# Generate from inspire-hep source (default: 10 documents, 1 question each, keypoint method)
kb eval generate --source-id inspire-hep

# Customize: 10 documents, 1 questions each (these are the deafult values)
kb eval generate --source-id inspire-hep --num-documents 10 --num-questions 1

# Use persona method instead
kb eval generate --source-id inspire-hep --strategy persona

# Process all documents (use with caution!)
kb eval generate --source-id inspire-hep --num-documents 0
```

### Python

```python
from test_mcp.kb.eval import generate_questions_from_source

# Generate questions from inspire-hep source
result = generate_questions_from_source(
    source_id="inspire-hep",
    num_documents=10,  # Default: 10 documents
    num_questions_per_doc=1,  # Default: 1 question per document
    generation_method="keypoint"  # Default: "keypoint"
)

generation_id = result["generation_id"]
print(f"Generated {result['num_questions_generated']} questions")
print(f"Generation ID: {generation_id}")
```

## Audit Questions

This step is optional but recommended to filter out invalid questions before running evaluations.

### CLI

**Automated LLM audit (recommended):**

```bash
# Audit all unaudited questions from a generation using LLM
kb eval audit --generation-id <generation_id> --llm

# Use a specific model for LLM auditing
kb eval audit --generation-id <generation_id> --llm --model gpt-4

# Audit all questions (no limit) using LLM
kb eval audit --generation-id <generation_id> --llm --limit 0
```

**Interactive human audit (without --llm flag):**

```bash
# Audit questions interactively in the console (default: 20 questions)
# Note: Without --llm, this will prompt you for each question
kb eval audit --generation-id <generation_id>

# Audit more questions interactively
kb eval audit --generation-id <generation_id> --limit 100
```

### Python

Audit questions using LLM (automated)

```python
from test_mcp.kb.eval import get_unaudited_questions, audit_question

# Get unaudited questions from a generation
questions = get_unaudited_questions(generation_id=generation_id)

# Audit all questions using LLM
for question in questions:
    audit = audit_question(
        question_id=question.id,
        model="gpt-4"  # Optional, defaults to EVAL_GEN_MODEL env var
    )
    print(f"Question {question.id}: {'Valid' if audit.is_valid else 'Invalid'}")
    print(f"  Comments: {audit.comments}")
```

## Create and Execute Evaluation Runs

### CLI

```bash
# Run evaluation (default: only valid questions)
kb eval run \
  --generation-id <generation_id> \
  --name "inspire-hep keypoints - all chunks" \
  --use-judge \
  --max-results 5

# Run with LLM judge
kb eval run \
  --generation-id <generation_id> \
  --name "inspire-hep keypoints - with judge" \
  --use-judge \
  --judge-model gpt-4 \
  --max-results 10

# Filter for questions that passed LLM judge audit
kb eval run \
  --generation-id <generation_id> \
  --name "inspire-hep personas - llm judge passed" \
  --max-results 5 \
  --use-judge \
  --audit-type llm_judge

# Include invalid questions (default: only valid questions are included)
kb eval run \
  --generation-id <generation_id> \
  --name "inspire-hep - include invalid" \
  --include-invalid \
  --max-results 10
```

### Python

#### Run 1: Summary chunks only

```python
from test_mcp.kb.eval import eval

# Run with summary chunks only
run1 = eval(
    name="inspire-hep keypoints - summary chunks",
    generation_id=generation_id,
    chunking_strategy="summary",
    max_results=10
)

print(f"Run 1 ID: {run1['run_id']}")
print(f"Hit rate: {run1['num_hits']}/{run1['num_questions']}")
```

#### Run 2: All chunk strategies (default)

```python
# Run with all chunk strategies (default, chunking_strategy=None)
run2 = eval(
    name="inspire-hep keypoints - all chunks",
    generation_id=generation_id,
    chunking_strategy=None,  # Uses all chunk strategies
    max_results=10
)

print(f"Run 2 ID: {run2['run_id']}")
print(f"Hit rate: {run2['num_hits']}/{run2['num_questions']}")
```

#### Run 3: Filter by audit type

```python
# Run evaluation only on questions that passed LLM judge audit
run3 = eval(
    name="inspire-hep personas - llm judge passed",
    generation_id=generation_id,
    audit_filters={
        "is_valid": True,  # Only valid questions
        "audit_type": "llm_judge"  # Only questions with llm_judge audit
    },
    max_results=5,
    use_llm_judge=True
)

print(f"Run 3 ID: {run3['run_id']}")
print(f"Hit rate: {run3['num_hits']}/{run3['num_questions']}")
```

## View Results

### Web Interface

Access results via web interface:
- Overview: `/web/eval`
- Generation: `/web/eval/generation/{generation_id}`
- Run details: `/web/eval/run/{run_id}`
- Question details: `/web/eval/question/{question_id}`
- Result details: `/web/eval/result/{result_id}`

### Python

```python
from test_mcp.kb.eval import get_summary_stats

stats = get_summary_stats(run_id=run1['run_id'])
print(f"Hit rate: {stats['hit_rate']:.2%}")
print(f"Document hits: {stats['num_hits']}/{stats['num_questions']}")
print(f"LLM judge hits: {stats['num_judge_hits']}/{stats['num_questions']}")
```
