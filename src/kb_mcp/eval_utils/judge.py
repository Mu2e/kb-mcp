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
#: extra correct detail; it also counted a value from a newer document as
#: correct, which proved gameable (any cited newer document was accepted).
#: 3 scores strictly against the reference again and keeps the source fields,
#: plus the DocDB numbers a differing value is attributed to, as flags only.
JUDGE_PROMPT_VERSION = 3

#: How an answer's key facts relate to the question's reference source. DocDB
#: is a living archive -- the same quantity is often reported differently in
#: talks and notes from different months -- so these flag a value that
#: differs from the reference because it came from another document. They do
#: not change the verdict: deciding that a reference is outdated takes human
#: research (see `kb eval conflicts`).
SOURCE_RELATIONS = (
    "matches_reference",   # agrees with the expected answer
    "newer_source",        # differs, attributed to a document newer than the reference
    "older_source",        # differs, attributed to an older document
    "conflicting_source",  # differs, attributed to another document of unclear age
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
            "differing_docs": list[int] | None,  # answer mode: DocDB numbers a differing value is attributed to
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
            "Score strictly against the expected answer: a key value that differs from it is incorrect,",
            "even if the answer attributes it to another document -- UNLESS the answer also states the",
            "expected value and says the documents disagree; then it is correct.",
            "",
            "Independently of the verdict, record where a differing value comes from. The knowledge base is",
            "a living archive: the same quantity is often reported differently in documents from different",
            "times. DocDB document numbers increase over time (a higher number is newer).",
            "- matches_reference: the answer's key values agree with the expected answer (extra detail, or",
            "  citing other documents for the same values, is still matches_reference).",
            "- newer_source / older_source / conflicting_source: ONLY when a key value DIFFERS from the",
            "  expected answer and the answer attributes it to a specific other document: newer or older",
            "  than the expected answer's source (compare DocDB numbers digit by digit: 52276 is LOWER,",
            "  i.e. older, than 54648), or of unclear age.",
            "- none: wrong or incomplete for any other reason (missing facts, misreading, no answer).",
            "List in differing_docs the DocDB numbers the answer attributes the DIFFERING value to (only",
            "those, as integers; empty if none).",
            "Set acknowledges_conflict to true whenever the answer itself points out that documents give",
            "different values.",
            "",
            "Respond with ONLY a valid JSON object:",
            "{",
            '  "is_hit": true or false,',
            '  "source_relation": "matches_reference" | "newer_source" | "older_source" | "conflicting_source" | "none",',
            '  "acknowledges_conflict": true or false,',
            '  "differing_docs": [DocDB numbers],',
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
        differing_docs = []
        if mode == "answer":
            source_relation = result.get("source_relation")
            if source_relation not in SOURCE_RELATIONS:
                source_relation = "none"
            acknowledges_conflict = bool(result.get("acknowledges_conflict", False))
            differing_docs = sorted({int(d) for d in (result.get("differing_docs") or [])
                                     if str(d).strip().isdigit()})
            # Strict scoring: a value attributed to another document is wrong
            # unless the answer also gives the reference value (acknowledged);
            # a verdict contradicting its own relation is overridden.
            if source_relation in ("newer_source", "older_source", "conflicting_source") \
                    and not acknowledges_conflict:
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
            "differing_docs": differing_docs if mode == "answer" else None,
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
