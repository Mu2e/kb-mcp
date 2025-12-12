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
) -> Dict:
    """Use LLM to judge if retrieved context answers the question.

    Generic LLM-as-judge implementation that evaluates whether provided
    context adequately answers a question. Can be used for any evaluation
    scenario (KB retrieval, RAG systems, QA systems, etc.).

    Args:
        question: The question to be answered
        retrieved_context: Context/information retrieved or provided
        expected_answer: Optional expected answer for comparison
        model: Optional model name (defaults to EVAL_JUDGE_MODEL env var)

    Returns:
        Dict with:
        {
            "is_hit": bool,  # Whether LLM judges the answer as correct
            "justification": str,  # Explanation of judgment
            "time_seconds": float,  # Time taken for judgment
        }

    Example:
        ```python
        result = llm_judge_answer(
            question="What is the flux?",
            retrieved_context="The measured flux is 42 units.",
            expected_answer="42 units"
        )
        print(result["is_hit"])  # True
        print(result["justification"])  # "The context clearly states..."
        ```
    """
    if model is None:
        eval_config = get_eval_config()
        model = eval_config['judge_model']

    client = get_openai_client()

    # Build prompt
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

    prompt = "\n".join(prompt_parts)

    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an evaluation judge assessing whether retrieved context answers questions. Always respond with valid JSON."
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
