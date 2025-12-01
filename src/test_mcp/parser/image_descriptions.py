"""Image description generation using LLMs."""

import base64
import os
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from .image_utils import detect_image_format

logger = logging.getLogger(__name__)


def _get_single_image_description(
    client, 
    document_text: str, 
    image_base64: str, 
    image_number: int,
    model: str
) -> str:
    """Get description for a single image using OpenAI client.
    
    Args:
        client: OpenAI client instance
        document_text: Full document text for context
        image_base64: Base64-encoded image string
        image_number: Image number (for context)
        model: Model name to use
        
    Returns:
        Image description string
    """
    # Detect image format
    image_format = detect_image_format(image_base64)

    # Create prompt with document context
    prompt = _create_image_description_prompt(document_text, image_number)

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

    return response.choices[0].message.content.strip()


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
        
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, skipping image descriptions")
            return image_dicts
        
        # Get model name and workers from environment variables
        model = os.getenv('PARSE_IMAGE_DESCRIPTION_MODEL', 'gpt-4o-mini')
        max_workers = int(os.getenv('PARSE_IMAGE_DESCRIPTION_NUMWORKERS', '6'))
        
        # Create OpenAI client with optional base URL
        client_kwargs = {'api_key': api_key}
        base_url = os.getenv('OPENAI_BASE_URL')
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
            image_number = img_dict["meta"]["image_number"]
            
            images_for_llm.append((idx, image_base64, image_number, img_dict))
        
        # Generate descriptions in parallel
        descriptions = [None] * len(images_for_llm)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_index = {
                executor.submit(
                    _get_single_image_description,
                    client,
                    document_text,
                    image_base64,
                    image_number,
                    model
                ): i
                for i, (_, image_base64, image_number, _) in enumerate(images_for_llm)
            }
            
            # Collect results
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    descriptions[index] = future.result()
                    img_number = images_for_llm[index][2]
                    logger.debug(f"✓ Image {img_number} description generated")
                except Exception as e:
                    logger.error(f"✗ Error getting description for image {images_for_llm[index][2]}: {e}")
                    descriptions[index] = "Image description unavailable"
        
        # Update image dicts with descriptions (modifies original dicts in image_dicts)
        for i, (_, _, image_number, img_dict) in enumerate(images_for_llm):
            description = descriptions[i]
            
            # Fill text field in image dict (updates original dict in image_dicts)
            img_dict["text"] = description
        
        return image_dicts
        
    except ImportError:
        logger.warning("openai package not installed, skipping image descriptions")
    except Exception as e:
        logger.error(f"Error generating image descriptions: {e}")
    
    return image_dicts


def _create_image_description_prompt(document_text: str, image_number: int) -> str:
    """Create prompt for image description based on document context.
    
    Args:
        document_text: Full document text (for context)
        image_number: Image number (for reference)
        
    Returns:
        Prompt string for image description
    """
    # Use the full document text as context (truncate if too long)
    # Limit to last 2000 characters to avoid token limits
    context = document_text[-2000:] if len(document_text) > 2000 else document_text

    return f"""Analyze this image (Image {image_number}) from the document and provide a description for document embedding and chat purposes.

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

