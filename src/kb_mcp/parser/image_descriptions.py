"""Image description generation using LLMs."""

import base64
import logging
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .image_utils import detect_image_format
from ..config import get_llm_config, get_parser_config
from ..llm.usage import STAGE_IMAGE_DESCRIPTION, UsageAccumulator

logger = logging.getLogger(__name__)


def _get_single_image_description(
    client,
    document_text: str,
    image_base64: str,
    image_identifier: str,
    model: str,
    img_meta: Dict[str, Any] | None = None,
    document_title: str | None = None,
) -> tuple[str, Any]:
    """Get description for a single image using OpenAI client.

    Args:
        client: OpenAI client instance
        document_text: Full document text (used as context fallback)
        image_base64: Base64-encoded image string
        image_identifier: Image identifier/name (e.g., "_page_3_Figure_3.png")
        model: Model name to use
        img_meta: Per-image meta from the parser. When present, the prompt
            uses meta["caption"] / nearby_text / page directly instead of
            re-deriving context by string-searching the markdown.
        document_title: Optional parent document title for grounding.

    Returns:
        (description, usage) — the description string and the response's raw
        `usage` object, so the caller can account for tokens without this
        helper touching the DB from inside a worker thread.
    """
    # Detect image format
    image_format = detect_image_format(image_base64)

    # Create prompt — meta-aware when meta is present, ±500-char fallback otherwise
    prompt = _create_image_description_prompt(
        document_text,
        image_identifier,
        img_meta=img_meta,
        document_title=document_title,
    )

    # Make request using OpenAI client. Reasoning models (gpt-oss-*) consume
    # max_tokens on internal thinking, so allow comfortable headroom for a
    # 2–4 sentence description.
    #
    # Qwen3.x is a reasoning-by-default model: without `enable_thinking=False`
    # it leaks chain-of-thought ("The user wants...", numbered analysis steps)
    # into output and ignores prompt instructions to skip preamble. The
    # `chat_template_kwargs` kwarg is the standard Qwen way to disable
    # thinking mode and is silently ignored by non-Qwen models, so it's safe
    # to pass unconditionally.
    extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{image_format};base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        max_tokens=800,
        extra_body=extra_body,
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Model returned empty response — vision may not be supported by this model")
    return content.strip(), getattr(response, "usage", None)


def _preflight_model(client, model: str) -> None:
    """Fail loudly, once, if `model` can't actually describe an image.

    Without this, a misconfigured model produces one failure per image inside
    the thread pool, each swallowed into an "Image description unavailable"
    placeholder — a whole document's figures degrade silently. One tiny
    round-trip up front turns that into a single actionable error before any
    work is done.

    Deliberately *not* `/v1/models`: the ALCF endpoint serves chat completions
    perfectly well while answering `/v1/models` with a 404, so a model listing
    is neither necessary nor sufficient evidence that inference works — and it
    can't tell a vision model from a text-only one that answers HTTP 200. The
    same 8x8 red PNG `kb-import --check-connections` uses settles both
    questions in one call.

    Transport-level failures are not treated as fatal: they'd fail the
    per-image calls anyway, with better context there. Only a model that
    answers and gets the test image wrong stops the run.

    Raises:
        ValueError: the model answered but could not identify the test image,
            i.e. it is not vision-capable at this endpoint.
    """
    from ..health import RED_PNG_B64, RED_WORDS

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What color is this image? Answer with one word."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{RED_PNG_B64}"},
                        },
                    ],
                }
            ],
            max_tokens=64,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    except Exception as e:
        logger.warning(
            "Image-description preflight could not reach the endpoint "
            "(%s: %s); continuing and letting the per-image calls decide.",
            type(e).__name__, e,
        )
        return

    content = (response.choices[0].message.content or "").strip()
    if not any(word in content.lower() for word in RED_WORDS):
        answered = content.replace("\n", " ")[:80] or "<empty>"
        raise ValueError(
            f"PARSE_IMAGE_DESCRIPTION_MODEL={model!r} could not identify the "
            f"test image (answered {answered!r}, expected 'red') — it is "
            f"probably text-only, or not the model you think it is. Set "
            f"PARSE_IMAGE_DESCRIPTION_MODEL to a vision-capable model, route "
            f"it with OPENAI_BASE_URL_MODELS, or run "
            f"`kb-import --check-connections` to see what the endpoint serves."
        )


def generate_image_descriptions(
    document_text: str,
    image_dicts: List[Dict[str, Any]],
    usage_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Generate descriptions for images in parallel and update image dicts.
    
    This function:
    - Extracts binary data from image_dicts and encodes to base64
    - Generates descriptions in parallel using LLM
    - Fills the 'text' field in each image_dict with the description
    
    Configuration is read from environment variables:
    - PARSE_IMAGE_DESCRIPTION_MODEL: Vision model name (default:
      DEFAULT_IMAGE_DESCRIPTION_MODEL). Must be served by the endpoint it
      routes to.
    - PARSE_IMAGE_DESCRIPTION_NUMWORKERS: Number of parallel workers (default: 6)
    - OPENAI_BASE_URL: Base URL for OpenAI API (optional)
    - OPENAI_BASE_URL_MODELS: JSON map of model -> base URL, so the vision model
      can live on a different host than the default chat model (optional)
    - OPENAI_API_KEY_MODELS: JSON map of model -> API key, for when that host
      needs its own credential (optional)
    - OPENAI_API_KEY: API key (required)
    
    Args:
        document_text: Text from the document (for context in description generation)
        image_dicts: List of image dictionaries (modified in place). Each dict should have:
            - 'binary': bytes (image binary data)
            - 'meta': dict with 'image_number' and optionally 'page'
        usage_out: Optional dict that receives the token-usage summary for
            this batch. Parsing runs before the document row exists, so the
            caller carries these counters forward and persists them once the
            document has an id.

    Returns:
        Updated image_dicts with 'text' field filled with descriptions
    """
    if not image_dicts:
        return image_dicts
    
    # Filter image dicts that have binary data
    images_with_binary = [
        (i, img_dict) for i, img_dict in enumerate(image_dicts)
        if img_dict["doc_type"] == 'image'
    ]
    
    if not images_with_binary:
        return image_dicts
    
    try:
        from openai import OpenAI

        llm_config = get_llm_config()
        parser_config = get_parser_config()
        api_key = llm_config['openai_api_key']
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping image descriptions")
            return image_dicts

        # Get model name and workers from configuration
        model = parser_config['image_description_model']
        max_workers = parser_config['image_description_num_workers']

        # Heads-up for models known to be text-only. These accept image_url
        # content without erroring and return a polite refusal at HTTP 200, so
        # descriptions silently become garbage — worse than a hard failure,
        # because nothing in the logs says anything went wrong.
        #
        # This list is advisory and endpoint-specific; the same name can be
        # vision-capable elsewhere. It is not a substitute for checking your
        # own endpoint. Verified against vllm.fnal.gov on 2026-08-25:
        # gpt-oss:120b answers "I can't see it from our current conversation".
        _TEXT_ONLY_MODELS = {
            "gpt-oss:120b",
            "openai/gpt-oss-120b",
            "codestral/codestral-latest",
            "qwen/qwen3-coder-30b",
        }
        if model in _TEXT_ONLY_MODELS:
            logger.warning(
                "PARSE_IMAGE_DESCRIPTION_MODEL=%s is known to be text-only; it "
                "will return refusals rather than real descriptions, with no "
                "error to signal it. Set PARSE_IMAGE_DESCRIPTION_MODEL to a "
                "vision-capable model, routing it to a suitable endpoint via "
                "OPENAI_BASE_URL_MODELS if it lives elsewhere.",
                model,
            )

        # Create OpenAI client. OPENAI_BASE_URL_MODELS maps individual models
        # to their own endpoint, so a vision model can live on a different
        # host than the default chat model — the same routing llm/llm.py does.
        # OPENAI_API_KEY_MODELS pairs with it: a model on someone else's host
        # needs that host's credential, not the global one.
        client_kwargs = {
            'api_key': llm_config['openai_api_key_models'].get(model, api_key)
        }
        base_url = llm_config['openai_base_url_models'].get(
            model, llm_config['openai_base_url']
        )
        if base_url:
            client_kwargs['base_url'] = base_url
            logger.debug(f"Using OpenAI base URL for image descriptions: {base_url}")

        client = OpenAI(**client_kwargs)

        # One loud failure beats one silent placeholder per image.
        _preflight_model(client, model)
        
        # Prepare images for description generation
        # Note: img_dict references point to the same dict objects in image_dicts (not copies)
        images_for_llm = []
        for idx, img_dict in images_with_binary:
            # Get binary data
            image_bytes = img_dict["binary"]

            # Encode to base64
            image_base64 = base64.b64encode(image_bytes).decode()
            # Use image_name from metadata (e.g., "_page_3_Figure_3.jpeg")
            # This is more descriptive than arbitrary numbering
            image_identifier = img_dict.get("meta", {}).get("image_name", f"image_{idx}")

            images_for_llm.append((idx, image_base64, image_identifier, img_dict))
        
        # Generate descriptions in parallel
        descriptions = [None] * len(images_for_llm)
        failures: List[str] = []
        usage = UsageAccumulator()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks. Pass each image's parser-extracted meta
            # (caption, nearby_text, page) so the prompt builder doesn't
            # have to re-derive context by string-search.
            future_to_index = {
                executor.submit(
                    _get_single_image_description,
                    client,
                    document_text,
                    image_base64,
                    image_identifier,
                    model,
                    img_dict.get("meta") or {},
                    None,  # document_title — caller can populate later
                ): i
                for i, (_, image_base64, image_identifier, img_dict) in enumerate(images_for_llm)
            }

            # Collect results
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    descriptions[index], call_usage = future.result()
                    usage.add(call_usage, stage=STAGE_IMAGE_DESCRIPTION, model=model)
                    img_id = images_for_llm[index][2]
                    logger.debug(f"Image '{img_id}' description generated")
                except Exception as e:
                    logger.error(f"Error getting description for image '{images_for_llm[index][2]}': {e}")
                    descriptions[index] = "Image description unavailable"
                    failures.append(str(e))

        # A handful of failures is per-image flakiness; a high rate is a
        # configuration problem (wrong model, wrong endpoint, dead host) that
        # would otherwise only show up as placeholder text in the UI.
        if failures:
            level = logger.error if len(failures) > len(images_for_llm) / 2 else logger.warning
            level(
                "%d/%d image descriptions failed (model=%s, base_url=%s). First error: %s",
                len(failures), len(images_for_llm), model, base_url or "<default>", failures[0],
            )

        # Update image dicts with descriptions (modifies original dicts in image_dicts).
        # The parser may have seeded `text` with nearby_text + caption already
        # — keep that prefix so the figure record carries surrounding-paragraph
        # context, caption, and VLM description all in one searchable string.
        # Order is paragraphs → caption → VLM description, matching the
        # parser's seeding order in parser_docling.py.
        for i, (_, _, image_identifier, img_dict) in enumerate(images_for_llm):
            description = descriptions[i]
            prefix = (img_dict.get("text") or "").strip()
            if prefix:
                img_dict["text"] = f"{prefix}\n\n{description}"
            else:
                img_dict["text"] = description
            # Also keep the VLM description on its own. `text` deliberately
            # bundles nearby_text + caption + description for retrieval, but
            # callers that want just the description — e.g. parse.py inlining
            # it into the parent document's text in place of the image — need
            # it without the surrounding-paragraph prefix, which would
            # otherwise duplicate prose that already sits next to the image.
            img_dict.setdefault("meta", {})["description"] = description

        logger.info(f"Image description tokens — {usage.format_summary()}")
        if usage_out is not None:
            usage_out.update(usage.summary())

        return image_dicts
        
    except ImportError:
        logger.warning("openai package not installed, skipping image descriptions")
    except Exception as e:
        logger.error(f"Error generating image descriptions: {e}")
    
    return image_dicts


def _create_image_description_prompt(
    document_text: str,
    image_identifier: str,
    img_meta: Dict[str, Any] | None = None,
    document_title: str | None = None,
) -> str:
    """Create prompt for image description.

    When `img_meta` carries parser-extracted context (caption, nearby_text,
    page), the prompt uses those directly — they're more reliable than
    re-deriving context by string-searching for the synthetic image
    identifier in the markdown export. Falls back to a ±500-char window
    around the identifier hit when meta is absent (legacy parsers /
    non-Docling routes).
    """
    caption = ""
    nearby_text = ""
    page = None
    if img_meta:
        caption = (img_meta.get("caption") or "").strip()
        nearby_text = (img_meta.get("nearby_text") or "").strip()
        page = img_meta.get("page")

    have_structured_context = bool(caption or nearby_text)

    if not have_structured_context:
        # Legacy fallback: ±500 chars around the identifier in the markdown.
        search_term = image_identifier.rsplit('.', 1)[0] if '.' in image_identifier else image_identifier
        pos = document_text.find(search_term)
        if pos != -1:
            context_window = 500
            start = max(0, pos - context_window)
            end = min(len(document_text), pos + len(search_term) + context_window)
            nearby_text = document_text[start:end]

    header_lines = []
    if document_title:
        if page is not None:
            header_lines.append(f'Source: page {page} of "{document_title}".')
        else:
            header_lines.append(f'Source: "{document_title}".')
    elif page is not None:
        header_lines.append(f"Source: page {page}.")
    header = "\n".join(header_lines)

    caption_block = f"Figure caption: {caption}" if caption else "Figure caption: (none)"
    nearby_block = f"Surrounding paragraphs:\n{nearby_text}" if nearby_text else "Surrounding paragraphs: (none)"

    return f"""You are describing a figure from a technical document for retrieval purposes. The description will be embedded into a vector index — accuracy matters, hallucination is worse than vague.

{header}
{caption_block}

{nearby_block}

[Image attached]

**Hard constraints — follow strictly**:
- Describe ONLY what you can clearly see. If you can't read fine details (axis values, small labels, dense data), do not guess. Say "axis values not legible at this resolution" rather than invent numbers.
- Do not state specific numerical values, ranges, or coordinates unless they are large, clearly printed, and obviously legible. Default: describe shapes and trends without numbers.
- Do not infer physics meaning from the caption — e.g. don't say "this is a calibration curve showing X improves Y" unless that interpretation is visually grounded in the image itself.
- Use the caption and surrounding text only to name subsystems/variables (e.g. "Cosmic Ray Veto module"), not to fabricate visual details.

**What to include (in order of priority)**:
1. Figure type: plot / schematic diagram / engineering drawing / photograph / screenshot / table-as-image / flowchart.
2. Layout: number of panels, axis labels (text only, not values), legend presence.
3. Visible structure: shapes, colors, labelled components.
4. Curve/data behaviour described qualitatively: "rising", "decaying to baseline", "two distinct peaks", "scatter with no clear trend" — never specific values.
5. Any clearly readable text labels in the image.

Output format:
- 2–4 sentences. Start directly with the content (no "This image shows"). No reasoning/thinking trace, no preamble. Just the description.
"""

