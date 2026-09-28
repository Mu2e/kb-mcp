"""LLM-based question generation for evaluation.

"""

import json
import logging
from typing import Dict, List, Optional

from ..llm import STAGE_EVAL_GENERATION, get_openai_client, parse_json_reply, record_llm_usage
from ..config import get_eval_config

logger = logging.getLogger(__name__)

# What makes a question usable in a retrieval benchmark. Shared by the question
# generators and the auditor (kb.eval.audit), so the two can't drift apart: a
# generator told only to write "natural" questions produced mostly
# document-internal ones ("the trolley assembly"), which the auditor rejected.
QUESTION_CRITERIA = """A good evaluation question must satisfy ALL of the following criteria:

1. **Self-contained**: The question makes sense without reading the source document. It must not rely on implicit context like "the dewar", "the module", "Table 3", "the klystron mentioned above", or "the device described earlier". A reader with general HEP knowledge should understand what is being asked.

2. **Externally motivated**: This is a question someone working on or studying the experiment would plausibly ask from the outside — about physics, detector design, computing systems, experimental methods, or engineering choices. It is NOT a reading-comprehension quiz on one specific document.

3. **Answerable from the knowledge base**: The answer should be findable in technical documents about the experiment (detector notes, technical reports, proceedings). It should have a specific, factual answer.

4. **Well-formed**: Clear, grammatically correct, and specific enough to have a definite answer.

Examples of GOOD questions:
- "What gas mixture is used in the Mu2e straw tracker?"
- "What is the readout scheme for the BaBar electromagnetic calorimeter?"
- "What clock frequency does the ATLAS Level-1 trigger operate at?"

Examples of BAD questions (fail self-containedness):
- "What is the maximum voltage of the forty feedthroughs in the dewar?" (assumes knowledge of which dewar)
- "What range of insertion trials is shown in Table 3?" (pure document reference)
- "What material is used for the component described in section 2.3?" (document-internal reference)"""


def format_document_context(title: Optional[str] = None, source_id: Optional[str] = None) -> str:
    """Header lines naming where a document comes from, for generation prompts.

    Without them the generator sees bare text and cannot name the experiment
    or subsystem a question is about.
    """
    lines = []
    if source_id:
        lines.append(f"Source: {source_id}")
    if title:
        lines.append(f"Title: {title}")
    return "\n".join(lines)

AGENTIC_TAGS = [
    "data_resurrection",
    "software_translation",
    "analysis_redo",
    "simulation_reconstruction",
    "calibration_recovery",
    "detector_understanding",
    "general",
]


def generate_qa_pairs_agentic(
    document_text: str,
    num_questions: int = 5,
    model: Optional[str] = None,
) -> Dict:
    """Generate questions an agentic system would ask an expert to reconstruct/revive old HEP work.

    Targets goals like data resurrection, software translation, re-doing analysis with new tools,
    and reconstructing simulations. Each question is tagged with a reconstruction category.

    Args:
        document_text: Document text to generate questions from
        num_questions: Number of questions to generate
        model: Optional model name (overrides EVAL_GEN_MODEL env var)

    Returns:
        Dict with 'qa_pairs' (list with 'question', 'tag', 'rationale'), 'type', 'model', 'prompt' keys

    Example:
        ```python
        result = generate_qa_pairs_agentic("The SLD detector used...", num_questions=3)
        result['qa_pairs'][0]
        # Returns: {'question': 'Which version of GEANT was used?', 'tag': 'simulation_reconstruction', 'rationale': '...'}
        ```
    """
    if model is None:
        eval_config = get_eval_config()
        model = eval_config['gen_model']

    client = get_openai_client(model)

    max_input_chars = 32000
    if len(document_text) > max_input_chars:
        logger.info(f"Truncating text from {len(document_text)} to {max_input_chars} characters")
        document_text = document_text[:max_input_chars]

    tags_list = "\n".join(f"- {t}" for t in AGENTIC_TAGS)

    user_prompt = f"""You are helping an AI agent perform data resurrection and modernization of old high-energy physics (HEP) work.
The agent's goals include: recovering data from old formats, translating legacy software to modern frameworks,
re-doing analyses with new tools, and reconstructing simulations.

Analyze the following document and generate {num_questions} precise, technical questions about concrete details
needed to reconstruct or revive this work.

Rules for questions:
- Ask about specific, concrete values, formats, or procedures found in or implied by the document
  (e.g. "What is the beam energy used in the run?" or "In which format is the polarization information stored?")
- Do NOT ask about people, authors, or who did what — focus purely on technical and scientific content
- Do NOT ask vague questions — each question should have a definite answer that could be looked up
- Do NOT reference tables, figures, or sections in the question (e.g. never say "According to Table I" or "As shown in Figure 3")
  — ask the question as if you don't know where in the document the answer lives
- If the document contains the answer, extract it; if the answer is missing or ambiguous in the document, leave "answer" as null
- Questions should be targeted to the specific content of this document, not generic HEP questions

For each question assign one tag from this list:
{tags_list}

Document:
{{document_text}}

Return ONLY a valid JSON object:
{{{{
  "qa_pairs": [
    {{{{
      "question": "...",
      "answer": "... or null if not found in the document",
      "tag": "<one of the tags above>",
      "rationale": "Why this detail is needed to reconstruct/revive the work (1 sentence)"
    }}}},
    ...
  ]
}}}}"""

    user_prompt_formatted = user_prompt.format(document_text=document_text)
    user_prompt_template = user_prompt.format(document_text="{document_text}")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert HEP physicist helping an AI agent reconstruct legacy experiments. Always respond with valid JSON."
                },
                {
                    "role": "user",
                    "content": user_prompt_formatted,
                }
            ],
            response_format={"type": "json_object"}
        )

        record_llm_usage(response.usage, stage=STAGE_EVAL_GENERATION, model=model,
                         meta={"strategy": "agentic"})
        content = response.choices[0].message.content.strip()
        result = parse_json_reply(content)
        qa_pairs = result.get("qa_pairs", [])

        valid_pairs = []
        for pair in qa_pairs:
            if "question" not in pair:
                logger.warning(f"Skipping invalid agentic QA pair: {pair}")
                continue
            valid_pairs.append({
                "question": pair["question"],
                "answer": pair.get("answer"),
                "tag": pair.get("tag", "general"),
                "rationale": pair.get("rationale", ""),
            })

        return {
            "qa_pairs": valid_pairs,
            "type": "qa_pairs_agentic",
            "model": model,
            "prompt": user_prompt_template,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        return {"qa_pairs": [], "type": "qa_pairs_agentic", "model": model, "prompt": user_prompt_template}
    except Exception as e:
        logger.error(f"Error generating agentic QA pairs: {e}")
        return {"qa_pairs": [], "type": "qa_pairs_agentic", "model": model, "prompt": user_prompt_template}


def generate_qa_pairs_keypoint(
    document_text: str,
    num_questions: int = 5,
    model: Optional[str] = None,
    document_context: str = "",
) -> Dict:
    """Generate question-keypoint pairs from document text.

    Single LLM call approach: extracts keypoints and generates questions together.

    Args:
        document_text: Document text to generate from
        num_questions: Number of Q&A pairs to generate
        model: Optional model name (overrides EVAL_GEN_MODEL env var)
        document_context: Optional header naming the document's source and
            title (see format_document_context), so questions can name what
            they are about instead of saying "the module".

    Returns:
        Dict with 'qa_pairs' (list), 'type', 'model', 'prompt' keys. Fewer than
        num_questions pairs come back when the document doesn't support that
        many questions meeting QUESTION_CRITERIA.

    Example:
        ```python
        result = generate_qa_pairs_keypoint("The flux is 42...", num_questions=3)
        result['qa_pairs'][0]
        # Returns: {'question': 'What is the measured flux?', 'keypoint': 'The flux is 42', 'answer': '42'}
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

    user_prompt = """Analyze the following document and generate up to {num_questions} question-keypoint pairs for a knowledge base retrieval benchmark.

For each pair:
1. Extract a key fact or important statement from the document (the "keypoint")
2. Write a question that someone working on or studying the experiment might ask, whose answer is this fact
3. Give a short, specific answer to the question, taken from the document

""" + QUESTION_CRITERIA.replace("{", "{{").replace("}", "}}") + """

To make questions self-contained, name what they are about: the experiment (e.g. Mu2e), the subsystem, and the specific component, using the source and title below where the text itself doesn't say. Never write "the assembly", "the detector", "this document", "the analysis" or similar references that only make sense next to the document.

Prefer facts that matter beyond this one document (design parameters, materials, methods, performance, decisions) over administrative details (who attended, document numbers, formatting). If the document does not support {num_questions} questions meeting all criteria, return fewer — even none. A short list of good questions is better than a full list with weak ones.

Other requirements:
- Be concise and clear, like a real user query
- Do not quote the keypoint verbatim in the question
- Cover different aspects of the document

{document_context}
Document:
{document_text}

Return ONLY a valid JSON object with a "qa_pairs" array (possibly empty):
{{
  "qa_pairs": [
    {{"question": "...", "keypoint": "...", "answer": "..."}}
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
                        document_context=document_context,
                        document_text=document_text
                        )
                }
            ],
            response_format={"type": "json_object"}
        )

        record_llm_usage(response.usage, stage=STAGE_EVAL_GENERATION, model=model,
                         meta={"strategy": "keypoint"})
        content = response.choices[0].message.content.strip()
        result = parse_json_reply(content)
        qa_pairs = result.get("qa_pairs", [])

        # Validate structure
        valid_pairs = []
        for pair in qa_pairs:
            if "question" in pair and "keypoint" in pair:
                valid_pairs.append({
                    "question": pair["question"],
                    "keypoint": pair["keypoint"],
                    "answer": pair.get("answer"),
                })
            else:
                logger.warning(f"Skipping invalid QA pair: {pair}")

        return {
            "qa_pairs": valid_pairs,
            "type": "qa_pairs_keypoint",
            "model": model,
            "prompt": user_prompt.format(
                num_questions=num_questions,
                document_context="{document_context}",
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
                document_context="{document_context}",
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
                document_context="{document_context}",
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

        record_llm_usage(response.usage, stage=STAGE_EVAL_GENERATION, model=model,
                         meta={"strategy": "persona"})
        content = response.choices[0].message.content.strip()
        result = parse_json_reply(content)
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