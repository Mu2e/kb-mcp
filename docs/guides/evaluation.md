# Evaluation

The evaluation module helps test and benchmark the quality of semantic search by generating synthetic questions from documents and measuring retrieval accuracy. See [database schema](database.md#evaluation) for details on how evaluation data is stored.

**Evaluation workflow:**

1. **Generate** synthetic questions from documents using LLM-based strategies
2. **Audit** (optional) - review and filter questions for quality
3. **Run** evaluation runs to test retrieval accuracy
4. **View** results via web interface or programmatically

## Generate Evaluation Dataset

### CLI

```bash
# Generate from source (default: 10 documents, 1 question each, keypoint method)
kb eval generate --source-id inspire-hep

# Customize generation parameters
kb eval generate --source-id inspire-hep --num-documents 10 --num-questions 1

# Use persona method instead of keypoint
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

## Audit Questions (Optional)

Review and filter generated questions before running evaluations.

### CLI

```bash
# Automated LLM audit (recommended)
kb eval audit --generation-id <generation_id> --llm

# Use specific model for auditing
kb eval audit --generation-id <generation_id> --llm --model gpt-4

# Interactive human audit
kb eval audit --generation-id <generation_id>

# Audit more questions interactively
kb eval audit --generation-id <generation_id> --limit 100
```

### Python

```python
from test_mcp.kb.eval import get_unaudited_questions, audit_question

# Get unaudited questions
questions = get_unaudited_questions(generation_id=generation_id)

# Audit using LLM
for question in questions:
    audit = audit_question(question_id=question.id, model="gpt-4")
    print(f"Question {question.id}: {'Valid' if audit.is_valid else 'Invalid'}")
```

## Run Evaluation

### CLI

```bash
# Basic evaluation run (uses valid questions only)
kb eval run --generation-id <generation_id> --name "my-eval-run" --max-results 5

# With LLM judge for answer quality assessment
kb eval run --generation-id <generation_id> --name "with-judge" --use-judge --judge-model gpt-4

# Filter by audit type
kb eval run --generation-id <generation_id> --name "llm-audited" --audit-type llm_judge

# Include invalid questions
kb eval run --generation-id <generation_id> --name "all-questions" --include-invalid
```

### Python

```python
from test_mcp.kb.eval import eval

# Basic evaluation run
result = eval(
    name="my-eval-run",
    generation_id=generation_id,
    max_results=10
)
print(f"Hit rate: {result['num_hits']}/{result['num_questions']}")

# With specific chunking strategy
result = eval(
    name="summary-chunks-only",
    generation_id=generation_id,
    chunking_strategy="summary",
    max_results=10
)

# With audit filters and LLM judge
result = eval(
    name="llm-audited-with-judge",
    generation_id=generation_id,
    audit_filters={"is_valid": True, "audit_type": "llm_judge"},
    use_llm_judge=True,
    max_results=5
)
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
