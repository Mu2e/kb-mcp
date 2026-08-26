"""Document summarization and gist generation using LLMs."""

import json
import logging
import re
import time
from typing import Dict, Optional

from ..llm import get_openai_client
from ..llm.usage import STAGE_DOCUMENT_SUMMARY, record_llm_usage
from ..config import get_llm_config

logger = logging.getLogger(__name__)


def _fix_json_escapes(json_str: str) -> str:
    """Fix common JSON escaping issues, particularly unescaped backslashes in strings.
    
    Args:
        json_str: JSON string that may have escaping issues
        
    Returns:
        Fixed JSON string
    """
    # Fix unescaped backslashes that aren't part of valid JSON escape sequences
    # Valid escapes: \\, \", \/, \b, \f, \n, \r, \t, \uXXXX
    # This regex matches \ followed by a character that isn't a valid escape sequence
    return re.sub(r'\\(?![\\"/bfnrtu0-9])', r'\\\\', json_str)


def summarize(
    text: str,
    include_gist: bool = True,
    include_summary: bool = True,
    include_title: bool = True,
    include_metadata: bool = False,
    model: Optional[str] = None,
    document_id: Optional[str] = None,
) -> Dict[str, str]:
    """Generate AI summary, gist, and/or title for a document.

    The title is a concise, descriptive title for the document.
    The gist contains high-level concepts and themes (useful for embedding context).
    The summary contains a detailed paragraph with key points and details for search/retrieval.

    Environment variables:
    - SUMMARY_MODEL: Model name (default: gemini-2.5-flash-lite')
    - OPENAI_BASE_URL: Base URL for OpenAI API (optional)
    - OPENAI_API_KEY: API key (required)

    Args:
        text: Document text to summarize
        include_gist: Whether to generate gist (default: True)
        include_summary: Whether to generate summary (default: True)
        include_title: Whether to generate title (default: False)
        include_metadata: Whether to extract structured metadata (default: False)
        model: Model name to use (overrides SUMMARY_MODEL env var)
        document_id: Document being summarized. Only used to attribute token
            usage; summarization itself doesn't need it.

    Returns:
        Dictionary with requested keys ('title', 'gist', 'summary'), 'time_summary' (seconds), and 'query' (prompt sent to LLM)

    Raises:
        ValueError: If OPENAI_API_KEY is not set
        ImportError: If openai package is not installed
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for summary generation")
        result = {"time_summary": 0.0, "query": ""}
        if include_title:
            result["title"] = ""
        if include_gist:
            result["gist"] = ""
        if include_summary:
            result["summary"] = ""
        return result

    # Get model from parameter or environment
    if model is None:
        llm_config = get_llm_config()
        model = llm_config['summary_model']

    client = get_openai_client(model)

    # Truncate text if too long (to avoid context limits)
    # For very large documents, use beginning where abstracts/introductions typically are
    # Rough estimate: 1 token ≈ 4 characters
    # Most models support 128k tokens, but keep conservative for cost/speed
    max_input_chars = 32000  # ~8k tokens, leaves plenty of room for response
    if len(text) > max_input_chars:
        logger.info(f"Truncating text from {len(text)} to {max_input_chars} characters (using beginning of document)")
        text = text[:max_input_chars]

    # Build prompt based on requested fields
    fields = []
    json_fields = []

    if include_title:
        fields.append('"title": A concise, descriptive title for the document (5-15 words).')
        json_fields.append('"title": "..."')

    if include_gist:
        fields.append('"gist": High-level concepts, themes, and key topics (around one sentence). Focus on what this document is fundamentally about - the core concepts and domain. This will be used as context for embeddings.')
        json_fields.append('"gist": "..."')

    if include_summary:
        fields.append('"summary": Detailed paragraph capturing key points, important details, findings, or arguments (up to ~350 words). Include specific information that would help someone understand the document\'s content. This will be used for search and retrieval.')
        json_fields.append('"summary": "..."')

    if include_metadata:
        fields.append('"metadata": Structured metadata object with keys: "event_datetime" (ISO string or null), "event_date" (YYYY-MM-DD or null), "event_name" (string or null), "event_participants" (array of strings), "event_organizations" (array of strings), "event_location" (string or null), "event_topics" (array of strings), "event_decisions" (array of strings), "event_action_items" (array of strings), "event_tags" (array of strings). Use null/empty arrays when unknown and do not invent facts.')
        json_fields.append('"metadata": {"event_datetime": null, "event_date": null, "event_name": null, "event_participants": [], "event_organizations": [], "event_location": null, "event_topics": [], "event_decisions": [], "event_action_items": [], "event_tags": []}')

    # Build numbered list of fields
    field_instructions = "\n\n".join(f"{i+1}. {field}" for i, field in enumerate(fields))
    json_format = '{' + ', '.join(json_fields) + '}'

    user_prompt = f"""Analyze the following document and provide a JSON object with:

{field_instructions}

Document:
{text}

Return ONLY a valid JSON object in this format:
{json_format}"""

    try:
        start_time = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant that creates concise summaries from documents. Always respond with valid JSON. Properly escape all special characters (use \\\\ for backslashes)."
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            #max_tokens=max_tokens,
            response_format={"type": "json_object"}  # Enforce JSON response
        )

        record_llm_usage(
            getattr(response, "usage", None),
            stage=STAGE_DOCUMENT_SUMMARY,
            model=model,
            document_id=document_id,
        )

        content = response.choices[0].message.content.strip()
        elapsed_time = time.time() - start_time

        # Try to extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Parse JSON response - fix common escaping issues if needed
        try:
            result = json.loads(content)
        except json.JSONDecodeError as parse_error:
            # Try to fix unescaped backslashes (common LLM issue, e.g., K\*0)
            logger.warning(f"JSON parse failed, attempting to fix escaping: {parse_error}")
            fixed_content = _fix_json_escapes(content)
            try:
                result = json.loads(fixed_content)
                logger.info("Successfully parsed JSON after fixing escape sequences")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON even after fixing: {parse_error}")
                logger.error(f"Raw content: {content}")
                raise parse_error
        result["time_summary"] = elapsed_time
        result["query"] = user_prompt

        # Validate expected keys
        if include_title and "title" not in result:
            logger.warning(f"Missing 'title' in response: {result.keys()}")
            result["title"] = ""

        if include_gist and "gist" not in result:
            logger.warning(f"Missing 'gist' in response: {result.keys()}")
            result["gist"] = ""

        if include_summary and "summary" not in result:
            logger.warning(f"Missing 'summary' in response: {result.keys()}")
            result["summary"] = ""

        if include_metadata and "metadata" not in result:
            logger.warning(f"Missing 'metadata' in response: {result.keys()}")
            result["metadata"] = {}

        logger.info(
            f"Generated summary ({len(result.get('summary', ''))} chars)"
            + (f" and gist ({len(result.get('gist', ''))} chars)" if include_gist else "")
            + f" in {elapsed_time:.2f}s"
        )

        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        logger.error(f"Raw content: {content}")
        # Return empty results on parse failure
        result = {"time_summary": time.time() - start_time, "query": user_prompt}
        if include_title:
            result["title"] = ""
        if include_gist:
            result["gist"] = ""
        if include_summary:
            result["summary"] = ""
        return result
    except Exception as e:
        logger.error(f"Error generating summary: {e}")
        raise