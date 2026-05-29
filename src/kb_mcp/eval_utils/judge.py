"""LLM-based answer quality judging for evaluation.

This module provides generic LLM judge functionality that can be used
in any evaluation context, not just KB retrieval.
"""

import json
import logging
import time
from typing import Dict, Optional

from ..llm import get_openai_client
from ..config import get_eval_config

logger = logging.getLogger(__name__)


def llm_judge_answer(
    question: str,
    retrieved_context: str,
    expected_answer: Optional[str] = None,
    model: Optional[str] = None,
    mode: str = "retrieval",
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

    Returns:
        Dict with:
        {
            "is_hit": bool,  # Whether LLM judges the answer as correct
            "justification": str,  # Explanation of judgment
            "time_seconds": float,  # Time taken for judgment
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
            prompt_parts.extend([
                "",
                f"Expected Answer: {expected_answer}",
            ])
        prompt_parts.extend([
            "",
            "Assess whether:",
            "1. The answer directly addresses the question",
            "2. The answer is factually accurate",
            "3. The answer is sufficiently complete",
            "",
            "Respond with ONLY a valid JSON object:",
            "{",
            '  "is_hit": true or false,',
            '  "justification": "Brief explanation of your assessment"',
            "}"
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

        content = response.choices[0].message.content.strip()
        result = json.loads(content)

        is_hit = result.get("is_hit", False)
        justification = result.get("justification", "")

        # Validate is_hit is boolean
        if not isinstance(is_hit, bool):
            logger.warning(f"Invalid is_hit type '{type(is_hit)}', defaulting to False")
            is_hit = False

        return {
            "is_hit": is_hit,
            "justification": justification,
            "time_seconds": judge_time,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM judge response: {e}")
        return {
            "is_hit": False,
            "justification": f"Error parsing judge response: {e}",
            "time_seconds": 0.0,
        }
    except Exception as e:
        logger.error(f"Error in LLM judge: {e}")
        return {
            "is_hit": False,
            "justification": f"Error: {e}",
            "time_seconds": 0.0,
        }
