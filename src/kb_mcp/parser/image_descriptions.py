"""Image description generation using LLMs."""

import base64
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .image_utils import detect_image_format
from ..config import get_llm_config, get_parser_config

logger = logging.getLogger(__name__)


def _get_single_image_description(
    client,
    document_text: str,
    image_base64: str,
    image_identifier: str,
    model: str
) -> str:
    """Get description for a single image using OpenAI client.

    Args:
        client: OpenAI client instance
        document_text: Full document text for context
        image_base64: Base64-encoded image string
        image_identifier: Image identifier/name (e.g., "_page_3_Figure_3.jpeg")
        model: Model name to use

    Returns:
        Image description string
    """
    # Detect image format
    image_format = detect_image_format(image_base64)

    # Create prompt with document context
    prompt = _create_image_description_prompt(document_text, image_identifier)

    # Make request using OpenAI client
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
        max_tokens=600  # Increased for detailed technical descriptions
    )

    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Model returned empty response — vision may not be supported by this model")
    return content.strip()


def generate_image_descriptions(
    document_text: str,
    image_dicts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate descriptions for images in parallel and update image dicts.
    
    This function:
    - Extracts binary data from image_dicts and encodes to base64
    - Generates descriptions in parallel using LLM
    - Fills the 'text' field in each image_dict with the description
    
    Configuration is read from environment variables:
    - PARSE_IMAGE_DESCRIPTION_MODEL: Model name (default: 'gpt-4o-mini')
    - PARSE_IMAGE_DESCRIPTION_NUMWORKERS: Number of parallel workers (default: 6)
    - OPENAI_BASE_URL: Base URL for OpenAI API (optional)
    - OPENAI_API_KEY: API key (required)
    
    Args:
        document_text: Text from the document (for context in description generation)
        image_dicts: List of image dictionaries (modified in place). Each dict should have:
            - 'binary': bytes (image binary data)
            - 'meta': dict with 'image_number' and optionally 'page'
    
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

        # Create OpenAI client with optional base URL
        client_kwargs = {'api_key': api_key}
        base_url = llm_config['openai_base_url']
        if base_url:
            client_kwargs['base_url'] = base_url

        client = OpenAI(**client_kwargs)
        
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
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks — use explicit loop (not dict comprehension) to
            # avoid late-binding closure capturing only the last loop values.
            future_to_index = {}
            for i, (orig_idx, image_base64, image_identifier, img_dict) in enumerate(images_for_llm):
                future = executor.submit(
                    _get_single_image_description,
                    client,
                    document_text,
                    image_base64,
                    image_identifier,
                    model,
                )
                future_to_index[future] = i

            # Collect results
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    descriptions[index] = future.result()
                    img_id = images_for_llm[index][2]
                    logger.debug(f"Image '{img_id}' description generated")
                except Exception as e:
                    logger.error(f"Error getting description for image '{images_for_llm[index][2]}': {e}")
                    descriptions[index] = "Image description unavailable"

        # Update image dicts with descriptions (modifies original dicts in image_dicts)
        for i, (_, _, image_identifier, img_dict) in enumerate(images_for_llm):
            description = descriptions[i]

            # Fill text field in image dict (updates original dict in image_dicts)
            img_dict["text"] = description
        
        return image_dicts
        
    except ImportError:
        logger.warning("openai package not installed, skipping image descriptions")
    except Exception as e:
        logger.error(f"Error generating image descriptions: {e}")
    
    return image_dicts


def _create_image_description_prompt(document_text: str, image_identifier: str) -> str:
    """Create prompt for image description based on document context.

    Args:
        document_text: Full document text (for context)
        image_identifier: Image identifier/name (e.g., "_page_3_Figure_3.jpeg")

    Returns:
        Prompt string for image description
    """
    # Try to find the image identifier in the document text to get relevant context
    # Search for the identifier (without extension) in the text
    search_term = image_identifier.rsplit('.', 1)[0] if '.' in image_identifier else image_identifier

    # Try to find where the image is referenced in the text
    pos = document_text.find(search_term)

    if pos != -1:
        # Found the image reference - extract context around it
        # Get ±500 characters around the reference
        context_window = 500
        start = max(0, pos - context_window)
        end = min(len(document_text), pos + len(search_term) + context_window)
        context = document_text[start:end]
    else:
        # Not found - we don't give any context
        context = ""

    return f"""Analyze this image ({image_identifier}) from the document and provide a description for document embedding and chat purposes.

Document context:
{context}

Instructions:
- If this is a technical diagram, graph, plot, chart, or schema: Provide detailed description including axes labels, data trends, key values, relationships shown, and technical details
- If this is a photo, artwork, or general image: Provide a broad but informative description of what's visible
- If this is a screenshot or interface: Describe the interface elements, layout, and functionality shown
- Keep the description informative but concise (2-4 sentences)
- Focus on information that would be useful for search and understanding the document content
- Do not include phrases like "This image shows" or "The image depicts" - start directly with the description

Description:"""

