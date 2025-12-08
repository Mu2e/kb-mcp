"""Evaluation module for knowledge base retrieval benchmarking.

This module provides tools for evaluating and benchmarking knowledge base retrieval:
- Question generation from documents
- Question auditing and quality control
- Evaluation run execution
- Metrics computation

Basic usage:
    >>> from test_mcp.kb.eval import generate_questions_from_documents, eval, get_summary_stats
    >>>
    >>> # Generate questions
    >>> result = generate_questions_from_documents(
    ...     document_ids=["doc-1", "doc-2"],
    ...     num_questions_per_doc=5,
    ...     generation_method="keypoint"
    ... )
    >>>
    >>> # Run evaluation
    >>> stats = eval(
    ...     name="Test embedding v1",
    ...     generation_id=result["generation_id"],
    ...     audit_filters={"is_valid": True}
    ... )
    >>>
    >>> # Get detailed stats
    >>> summary = get_summary_stats(stats["run_id"])
    >>> print(f"Hit rate: {summary['hit_rate']:.2%}")
"""

# Question generation
from .generation import (
    generate_questions_from_documents,
    generate_questions_from_source,
)

# Question auditing
from .audit import (
    add_audit,
    audit_question,
    get_question_audits,
    get_unaudited_questions,
)

# Evaluation execution
from .runner import (
    create_eval_run,
    execute_eval_run,
    eval,
    get_run_results,
)
from .core import get_eval_run, get_or_create_eval_generation

# Metrics
from .metrics import (
    compute_hit_rate,
    get_rank_distribution,
    get_summary_stats,
)

__all__ = [
    # Generation
    "generate_questions_from_documents",
    "generate_questions_from_source",
    # Auditing
    "add_audit",
    "audit_question",
    "get_question_audits",
    "get_unaudited_questions",
    # Execution
    "create_eval_run",
    "execute_eval_run",
    "eval",
    "get_run_results",
    "get_eval_run",
    "get_or_create_eval_generation",
    # Metrics
    "compute_hit_rate",
    "get_rank_distribution",
    "get_summary_stats",
]
