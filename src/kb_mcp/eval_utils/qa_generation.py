"""LLM-based question generation for evaluation.

"""

import json
import logging
from typing import Dict, List, Optional

from ..llm import get_openai_client
from ..config import get_eval_config

logger = logging.getLogger(__name__)


def generate_qa_pairs_keypoint(
    document_text: str,
    num_questions: int = 5,
    model: Optional[str] = None,
) -> Dict:
    """Generate question-keypoint pairs from document text.

    Single LLM call approach: extracts keypoints and generates questions together.

    Args:
        document_text: Document text to generate from
        num_questions: Number of Q&A pairs to generate
        model: Optional model name (overrides EVAL_GEN_MODEL env var)

    Returns:
        Dict with 'qa_pairs' (list), 'type', 'model', 'prompt' keys

    Example:
        ```python
        result = generate_qa_pairs_keypoint("The flux is 42...", num_questions=3)
        result['qa_pairs'][0]
        # Returns: {'question': 'What is the measured flux?', 'keypoint': 'The flux is 42'}
        ```
    """
    if model is None:
        eval_config = get_eval_config()
        model = eval_config['gen_model']

    client = get_openai_client(model)

    # Truncate text if too long
    max_input_chars = 32000  # ~8k tokens
    if len(document_text) > max_input_chars:
        logger.info(f"Truncating text from {len(document_text)} to {max_input_chars} characters")
        document_text = document_text[:max_input_chars]

    user_prompt = """Analyze the following document and generate {num_questions} question-keypoint pairs for evaluation.

For each pair:
1. Extract a key fact or important statement from the document (the "keypoint")
2. Generate a natural question that someone might ask to find this information

Document:
{document_text}

The questions should:
- Be natural and realistic (like real user queries)
- Be concise and clear
- Not directly quote the keypoint verbatim
- Cover different aspects of the document

Return ONLY a valid JSON object with a "qa_pairs" array:
{{
  "qa_pairs": [
    {{"question": "...", "keypoint": "..."}},
    {{"question": "...", "keypoint": "..."}}
  ]
}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that generates realistic evaluation questions with their corresponding key facts. Always respond with valid JSON."
                },
                {
                    "role": "user",
                    "content": user_prompt.format(
                        num_questions=num_questions,
                        document_text=document_text
                        )
                }
            ],
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)
        qa_pairs = result.get("qa_pairs", [])

        # Validate structure
        valid_pairs = []
        for pair in qa_pairs:
            if "question" in pair and "keypoint" in pair:
                valid_pairs.append({
                    "question": pair["question"],
                    "keypoint": pair["keypoint"]
                })
            else:
                logger.warning(f"Skipping invalid QA pair: {pair}")

        return {
            "qa_pairs": valid_pairs,
            "type": "qa_pairs_keypoint",
            "model": model,
            "prompt": user_prompt.format(
                num_questions=num_questions,
                document_text="{document_text}"
            )
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        logger.error(f"Raw content: {content if 'content' in locals() else 'N/A'}")
        return {
            "qa_pairs": [],
            "type": "qa_pairs_keypoint",
            "model": model,
            "prompt": user_prompt.format(
                num_questions=num_questions,
                document_text="{document_text}"
            )
        }
    except Exception as e:
        logger.error(f"Error generating QA pairs: {e}")
        return {
            "qa_pairs": [],
            "type": "qa_pairs_keypoint",
            "model": model,
            "prompt": user_prompt.format(
                num_questions=num_questions,
                document_text="{document_text}"
            )
        }


def generate_qa_pairs_persona(
    document_text: str,
    num_questions: int = 3,
    personas: Optional[List[str]] = None,
    model: Optional[str] = None,
) -> Dict:
    """Generate question-answer pairs with different user personas.

    Inspired by chATLAS approach: generates questions from different user perspectives.
    Total questions generated = num_questions × len(personas).

    Args:
        document_text: Document text to generate from
        num_questions: Number of Q&A pairs to generate PER PERSONA
        personas: List of persona names (default: ['early_career', 'established_worker', 'experienced_professional'])
        model: Optional model name (overrides EVAL_GEN_MODEL env var)

    Returns:
        Dict with 'qa_pairs' (list), 'type', 'model', 'prompt' keys

    Example:
        ```python
        result = generate_qa_pairs_persona("...", num_questions=2, personas=['beginner', 'expert'])
        result['qa_pairs'][0]
        # Returns: {'question': 'What is flux?', 'answer': 'A measure of...', 'persona': 'beginner'}
        len(result['qa_pairs'])  # 2 questions × 2 personas = 4 total
        # Returns: 4
        ```
    """
    if model is None:
        eval_config = get_eval_config()
        model = eval_config['gen_model']

    if personas is None:
        personas = ['early_career', 'established_worker', 'experienced_professional']

    client = get_openai_client(model)

    # Truncate text if too long
    max_input_chars = 32000
    if len(document_text) > max_input_chars:
        logger.info(f"Truncating text from {len(document_text)} to {max_input_chars} characters")
        document_text = document_text[:max_input_chars]

    user_prompt = """Analyze the following document and generate {num_questions} question-answer pairs for each of these user personas:

Personas:
{personas}

For each persona, generate {num_questions} questions that:
- Reflect what that type of user would realistically ask
- Are answerable from the document
- Vary in specificity and complexity based on the persona
- Focus in particl physcis (experiment) technical details

Document:
{document_text}

Return ONLY a valid JSON object with persona-based Q&A pairs:
{{
  "persona_qa_pairs": [
    {{"persona": "early_career", "question": "...", "answer": "..."}},
    {{"persona": "established_worker", "question": "...", "answer": "..."}},
    ...
  ]
}}"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that generates realistic user questions from different perspectives. Always respond with valid JSON."
                },
                {
                    "role": "user",
                    "content": user_prompt.format(
                        num_questions=num_questions,
                        personas=json.dumps(personas, indent=2),
                        document_text=document_text
                    )
                }
            ],
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content.strip()
        result = json.loads(content)
        qa_pairs = result.get("persona_qa_pairs", [])

        # Validate structure
        valid_pairs = []
        for pair in qa_pairs:
            if "question" in pair and "persona" in pair:
                valid_pairs.append({
                    "question": pair["question"],
                    "answer": pair.get("answer"),  # Optional
                    "persona": pair["persona"]
                })
            else:
                logger.warning(f"Skipping invalid QA pair: {pair}")

        # Store prompt template (not the formatted version)
        prompt_template = """Analyze the following document and generate {num_questions} question-answer pairs for each of these user personas:

Personas:
{personas}

For each persona, generate {num_questions} questions that:
- Reflect what that type of user would realistically ask
- Are answerable from the document
- Vary in specificity and complexity based on the persona

Document:
{document_text}

Return ONLY a valid JSON object with persona-based Q&A pairs:
{{
  "persona_qa_pairs": [
    {{"persona": "early_career", "question": "...", "answer": "..."}},
    {{"persona": "established_worker", "question": "...", "answer": "..."}},
    ...
  ]
}}"""

        return {
            "qa_pairs": valid_pairs,
            "type": "qa_pairs_persona",
            "model": model,
            "prompt": prompt_template
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        logger.error(f"Raw content: {content if 'content' in locals() else 'N/A'}")
        prompt_template = """Analyze the following document and generate {num_questions} question-answer pairs for each of these user personas:

Personas:
{personas}

For each persona, generate {num_questions} questions that:
- Reflect what that type of user would realistically ask
- Are answerable from the document
- Vary in specificity and complexity based on the persona

Document:
{document_text}

Return ONLY a valid JSON object with persona-based Q&A pairs:
{{
  "persona_qa_pairs": [
    {{"persona": "early_career", "question": "...", "answer": "..."}},
    {{"persona": "established_worker", "question": "...", "answer": "..."}},
    ...
  ]
}}"""
        return {
            "qa_pairs": [],
            "type": "qa_pairs_persona",
            "model": model,
            "prompt": prompt_template
        }
    except Exception as e:
        logger.error(f"Error generating persona QA pairs: {e}")
        prompt_template = """Analyze the following document and generate {num_questions} question-answer pairs for each of these user personas:

Personas:
{personas}

For each persona, generate {num_questions} questions that:
- Reflect what that type of user would realistically ask
- Are answerable from the document
- Vary in specificity and complexity based on the persona

Document:
{document_text}

Return ONLY a valid JSON object with persona-based Q&A pairs:
{{
  "persona_qa_pairs": [
    {{"persona": "early_career", "question": "...", "answer": "..."}},
    {{"persona": "established_worker", "question": "...", "answer": "..."}},
    ...
  ]
}}"""
        return {
            "qa_pairs": [],
            "type": "qa_pairs_persona",
            "model": model,
            "prompt": prompt_template
        }