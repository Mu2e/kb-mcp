"""LLM-based answer quality judging for evaluation.

This module provides generic LLM judge functionality that can be used
in any evaluation context, not just KB retrieval.
"""

import json
import logging
import time
from typing import Dict, Optional

from ..llm import STAGE_EVAL_JUDGE, GatewayLimitExceeded, get_openai_client, parse_json_reply, record_llm_usage
from ..config import get_eval_config

logger = logging.getLogger(__name__)

#: Version of the answer-mode judge prompt; recorded in each run's
#: judge_strategy (and run name) so verdicts from different prompts never mix.
#: 2 added source_relation / acknowledges_conflict and stopped penalising
#: extra correct detail.
JUDGE_PROMPT_VERSION = 2

#: How an answer's key facts relate to the question's reference source. DocDB
#: is a living archive -- the same quantity is often reported differently in
#: talks and notes from different months -- so a value that differs from the
#: reference is not necessarily wrong; these keep that case visible.
SOURCE_RELATIONS = (
    "matches_reference",   # agrees with the expected answer
    "newer_source",        # differs, taken from a document newer than the reference: correct
    "older_source",        # differs, taken from an older (outdated) document: wrong
    "conflicting_source",  # differs, from another document of unclear age
    "none",                # no other source involved (plainly wrong, or no answer)
)


def llm_judge_answer(
    question: str,
    retrieved_context: str,
    expected_answer: Optional[str] = None,
    model: Optional[str] = None,
    mode: str = "retrieval",
    reference_source: Optional[str] = None,
) -> Dict:
    """Use LLM to judge if an answer or retrieved context answers the question.

    Generic LLM-as-judge implementation. Supports two modes:
    - "retrieval": judges whether retrieved context contains the answer (semantic/hybrid/fulltext)
    - "answer": judges whether an LLM-generated answer is correct (llm_only/rag/agentic)

    Args:
        question: The question to be answered
        retrieved_context: Context retrieved or LLM-generated answer to evaluate
        expected_answer: Optional expected answer for comparison
        model: Optional model name (defaults to EVAL_JUDGE_MODEL env var)
        mode: "retrieval" (default) or "answer"
        reference_source: For answer mode, the document the expected answer
            comes from (e.g. "mu2e-docdb 52249 'Trigger meeting introduction',
            2025-04-01"), so the judge can tell a newer source from an older one.

    Returns:
        Dict with:
        {
            "is_hit": bool | None,  # Whether the judge accepts the answer; None if it could not judge
            "justification": str,  # Explanation of judgment
            "time_seconds": float,  # Time taken for judgment
            "source_relation": str | None,  # answer mode: one of SOURCE_RELATIONS
            "acknowledges_conflict": bool | None,  # answer mode: answer notes disagreeing sources
        }
    """
    if model is None:
        eval_config = get_eval_config()
        model = eval_config['judge_model']

    client = get_openai_client(model)

    if mode == "answer":
        prompt_parts = [
            "Evaluate whether the following answer correctly answers the question.",
            "",
            f"Question: {question}",
            "",
            f"Answer:\n{retrieved_context}",
        ]
        if expected_answer:
            prompt_parts.extend(["", f"Expected Answer: {expected_answer}"])
        if reference_source:
            prompt_parts.extend(["", f"The expected answer comes from: {reference_source}"])
        prompt_parts.extend([
            "",
            "Judge the facts the question asks for. Extra correct detail or context beyond the expected",
            "answer does not make an answer wrong; a missing or incorrect requested fact does.",
            "",
            "The knowledge base is a living archive: the same quantity is often reported differently in",
            "documents from different times. DocDB document numbers increase over time, so a higher",
            "DocDB number means a newer document. When the answer's key value differs from the expected",
            "answer, decide from the documents the answer cites:",
            "- taken from a document NEWER than the expected answer's source: correct (newer_source)",
            "- taken from a document OLDER than the expected answer's source: incorrect (older_source)",
            "- taken from another document whose age relative to the source is unclear: incorrect",
            "  (conflicting_source) -- UNLESS the answer states both values, says the documents disagree,",
            "  and one of the stated values matches the expected answer; then it is correct.",
            "Set acknowledges_conflict to true whenever the answer itself points out that documents give",
            "different values, whichever value it ends up favouring.",
            "",
            "Choosing source_relation:",
            "- matches_reference: the answer's key values agree with the expected answer (extra detail,",
            "  or citing other documents for the same values, is still matches_reference).",
            "- newer_source / older_source / conflicting_source: ONLY when a key value DIFFERS from the",
            "  expected answer because the answer took it from a different document. Compare DocDB",
            "  numbers digit by digit: e.g. 52276 is LOWER (older) than 54648.",
            "- none: the answer is wrong or incomplete for any other reason (missing facts, misreading,",
            "  no answer) -- this is not a source conflict.",
            "",
            "Respond with ONLY a valid JSON object:",
            "{",
            '  "is_hit": true or false,',
            '  "source_relation": "matches_reference" | "newer_source" | "older_source" | "conflicting_source" | "none",',
            '  "acknowledges_conflict": true or false,',
            '  "justification": "Brief explanation of your assessment"',
            "}",
        ])
        system_prompt = "You are an evaluation judge assessing whether an LLM-generated answer correctly answers a question. Always respond with valid JSON."
    else:
        prompt_parts = [
            "Evaluate whether the following retrieved context adequately answers the question.",
            "",
            f"Question: {question}",
            "",
            f"Retrieved Context:\n{retrieved_context}",
        ]
        if expected_answer:
            prompt_parts.extend([
                "",
                f"Expected Answer: {expected_answer}",
            ])
        prompt_parts.extend([
            "",
            "Assess whether:",
            "1. The context contains information that answers the question",
            "2. The answer is accurate and relevant",
            "3. The answer is complete enough to be useful",
            "",
            "Respond with ONLY a valid JSON object:",
            "{",
            '  "is_hit": true or false,',
            '  "justification": "Brief explanation of your assessment"',
            "}"
        ])
        system_prompt = "You are an evaluation judge assessing whether retrieved context answers questions. Always respond with valid JSON."

    prompt = "\n".join(prompt_parts)

    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            response_format={"type": "json_object"}
        )
        judge_time = time.time() - start_time
        record_llm_usage(response.usage, stage=STAGE_EVAL_JUDGE, model=model)

        content = response.choices[0].message.content.strip()
        result = parse_json_reply(content)

        is_hit = result.get("is_hit", False)
        justification = result.get("justification", "")
        source_relation = acknowledges_conflict = None
        if mode == "answer":
            source_relation = result.get("source_relation")
            if source_relation not in SOURCE_RELATIONS:
                source_relation = "none"
            acknowledges_conflict = bool(result.get("acknowledges_conflict", False))
            # The source rules decide these cases; a verdict contradicting its
            # own relation (seen with weaker judges) is overridden.
            if source_relation == "older_source":
                is_hit = False
            elif source_relation == "conflicting_source" and not acknowledges_conflict:
                is_hit = False

        # Validate is_hit is boolean
        if not isinstance(is_hit, bool):
            logger.warning(f"Invalid is_hit type '{type(is_hit)}', defaulting to False")
            is_hit = False

        return {
            "is_hit": is_hit,
            "justification": justification,
            "time_seconds": judge_time,
            "source_relation": source_relation,
            "acknowledges_conflict": acknowledges_conflict,
        }

    except GatewayLimitExceeded:
        # Not a verdict and not retryable now: let the run stop.
        raise
    except json.JSONDecodeError as e:
        # is_hit None = not judged, so a broken reply never counts as "wrong".
        logger.error(f"Failed to parse LLM judge response: {e}")
        return {
            "is_hit": None,
            "justification": f"Error parsing judge response: {e}",
            "time_seconds": 0.0,
            "source_relation": None,
            "acknowledges_conflict": None,
        }
    except Exception as e:
        logger.error(f"Error in LLM judge: {e}")
        return {
            "is_hit": None,
            "justification": f"Error: {e}",
            "time_seconds": 0.0,
            "source_relation": None,
            "acknowledges_conflict": None,
        }
