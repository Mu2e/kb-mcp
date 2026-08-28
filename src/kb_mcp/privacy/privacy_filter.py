"""LLM-based privacy classification for documents."""

import json
import logging
import re
import time
from typing import Dict, Optional

from ..llm import get_openai_client
from ..llm.usage import STAGE_PRIVACY_FILTER, record_llm_usage
from ..config import get_llm_config

logger = logging.getLogger(__name__)

LABEL_PUBLIC = "public"
LABEL_NEEDS_REVIEW = "needs_review"
LABEL_PRIVATE = "private"
VALID_LABELS = {LABEL_PUBLIC, LABEL_NEEDS_REVIEW, LABEL_PRIVATE}

_SYSTEM_PROMPT = """\
You are a privacy classifier for scientific and technical documents. \
Your task is to assess whether a document contains private or sensitive information \
that should not be published publicly. Always respond with valid JSON."""

_USER_PROMPT_TEMPLATE = """\
Analyze the following document text and classify its privacy level.

Assign one of three labels:
- "public": The document contains no personal or private information. It is safe to publish as-is. \
Typical examples: technical reports, scientific papers, reference documents, meeting agendas with only technical content.
- "needs_review": The document may contain some sensitive information but it is not clearly private. \
A human should review it before publishing. Typical examples: documents with names/affiliations of people, \
internal project discussions, budget information, draft content not intended for wide release.
- "private": The document clearly contains private or sensitive information that should NOT be published. \
Typical examples: personal emails or messages between individuals, HR/personnel records, private conversations, \
home addresses or personal contact details, confidential internal communications, medical or legal information.

Focus especially on:
- Personal conversations or messages between people
- Personal contact details (addresses, phone numbers, personal emails)
- Personnel or HR information
- Internal financial or budget details
- Clearly confidential internal discussions not intended for public release

Document text:
{text}

Return ONLY a valid JSON object in this exact format:
{{"label": "<public|needs_review|private>", "reasoning": "<1-3 sentence explanation of why this label was chosen>"}}"""


def classify_privacy(
    text: str,
    model: Optional[str] = None,
    document_id: Optional[str] = None,
    raw_document_id: Optional[str] = None,
) -> Dict[str, str]:
    """Classify a document's privacy level using an LLM.

    Args:
        text: Document text to classify (will be truncated to 32k chars)
        model: Model to use (overrides PRIVACY_FILTER_MODEL or DEFAULT_LLM_MODEL env var)
        document_id: Parsed document classified. Only used to attribute tokens.
        raw_document_id: Source file classified. Only used to attribute tokens.

    Returns:
        Dictionary with:
        - label: 'public', 'needs_review', or 'private'
        - reasoning: LLM explanation for the classification
        - time_seconds: Time taken in seconds
        - model: Model name actually used
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for privacy classification")
        return {
            "label": LABEL_NEEDS_REVIEW,
            "reasoning": "Document has no text content; flagged for manual review.",
            "time_seconds": 0.0,
            "model": model or "",
        }

    if model is None:
        llm_config = get_llm_config()
        model = llm_config.get("privacy_filter_model") or llm_config.get("eval_judge_model") or llm_config["default_model"]

    client = get_openai_client(model)

    max_input_chars = 32000
    if len(text) > max_input_chars:
        logger.info(
            f"Truncating text from {len(text)} to {max_input_chars} characters for privacy classification"
        )
        text = text[:max_input_chars]

    user_prompt = _USER_PROMPT_TEMPLATE.format(text=text)

    start_time = time.time()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        elapsed = time.time() - start_time

        record_llm_usage(
            getattr(response, "usage", None),
            stage=STAGE_PRIVACY_FILTER,
            model=model,
            document_id=document_id,
            raw_document_id=raw_document_id,
        )

        content = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        result = json.loads(content)

        label = result.get("label", "").strip().lower()
        if label not in VALID_LABELS:
            logger.warning(f"Unexpected label '{label}' from LLM, defaulting to needs_review")
            label = LABEL_NEEDS_REVIEW

        reasoning = result.get("reasoning", "").strip()

        logger.info(f"Privacy classification: {label} ({elapsed:.2f}s)")

        return {
            "label": label,
            "reasoning": reasoning,
            "time_seconds": elapsed,
            "model": model,
        }

    except json.JSONDecodeError as e:
        elapsed = time.time() - start_time
        logger.error(f"Failed to parse JSON response for privacy classification: {e}")
        return {
            "label": LABEL_NEEDS_REVIEW,
            "reasoning": f"Classification failed (JSON parse error); flagged for manual review.",
            "time_seconds": elapsed,
            "model": model,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Privacy classification error: {e}")
        raise
