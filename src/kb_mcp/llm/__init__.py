"""LLM client utilities."""

from .llm import get_openai_client, parse_json_reply
from .retry import RateLimited, call_with_backoff, is_throttling
from .usage import (
    STAGE_DOCUMENT_SUMMARY,
    STAGE_EMBEDDING,
    STAGE_EVAL_ANSWER,
    STAGE_EVAL_AUDIT,
    STAGE_EVAL_GENERATION,
    STAGE_EVAL_JUDGE,
    STAGE_GRAPH_EXTRACTION,
    STAGE_GRAPH_MATCHING,
    STAGE_IMAGE_DESCRIPTION,
    STAGE_PRIVACY_FILTER,
    STAGE_TABLE_SUMMARY,
    UsageAccumulator,
    record_llm_usage,
    usage_snapshot,
)

__all__ = [
    'get_openai_client',
    'parse_json_reply',
    'RateLimited',
    'call_with_backoff',
    'is_throttling',
    'record_llm_usage',
    'usage_snapshot',
    'UsageAccumulator',
    'STAGE_TABLE_SUMMARY',
    'STAGE_IMAGE_DESCRIPTION',
    'STAGE_DOCUMENT_SUMMARY',
    'STAGE_GRAPH_EXTRACTION',
    'STAGE_GRAPH_MATCHING',
    'STAGE_PRIVACY_FILTER',
    'STAGE_EMBEDDING',
    'STAGE_EVAL_GENERATION',
    'STAGE_EVAL_AUDIT',
    'STAGE_EVAL_ANSWER',
    'STAGE_EVAL_JUDGE',
]
