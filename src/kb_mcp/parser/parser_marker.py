"""Marker parser for high-quality PDF to markdown conversion."""

import logging
import time
import io
import re
import os
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from .parser_base import BaseParser

logger = logging.getLogger(__name__)

# Module-level cache for the converter
_CONVERTER_CACHE = None

def _get_converter() -> Any:
    """Initialize and return the marker PdfConverter (cached)."""
    global _CONVERTER_CACHE
    if _CONVERTER_CACHE is not None:
        return _CONVERTER_CACHE

    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except ImportError:
        logger.error("marker-pdf not installed. Install with: pip install \"marker-pdf[all]\"")
        return None

    logger.info("Initializing marker PdfConverter (this may take a while)...")

    try:
        artifact_dict = create_model_dict()
        config = {
            "output_format": "markdown",
            "disable_image_extraction": False,
            "languages": "en",
            "batch_multiplier": 15, # Tested on NERSC a100 up to 30, don't really see a difference, GPU mem saturates at 40%
        }
        _CONVERTER_CACHE = PdfConverter(artifact_dict=artifact_dict, config=config)
        return _CONVERTER_CACHE
    except (OSError, PermissionError) as e:
        if "Read-only file system" in str(e):
            logger.error(
                "Failed to initialize marker: Cannot write to read-only package directory. "
                "Please install marker-pdf in a writable location (e.g., venv in $SCRATCH or $HOME). "
                f"Error: {e}"
            )
        else:
            logger.error(f"Failed to initialize marker: {e}")
        return None


class MarkerParser(BaseParser):
    """Parser for PDF documents using marker-pdf."""

    def extract_text(self) -> str:
        """Extract text using marker (uses extract_text_and_images_dict internally)."""
        text, _ = self.extract_text_and_images_dict({})
        return text

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract text and images from PDF using marker-pdf.
        
        Args:
            parent_data: Dictionary with parent document data
           
        Returns:
            Tuple of (markdown_text, list_of_image_dicts)
        """
        converter = _get_converter()
        if converter is None:
            return "", []

        try:
            from io import BytesIO
            
            # Parse document
            rendered = converter(str(self.file_path))
            text = rendered.markdown

            # Track where each image is referenced in markdown text.
            # This enables downstream workflows to map image documents back
            # to approximate positions in the parent text.
            image_reference_info = {}
            image_tag_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
            image_ref_counter = 0
            for match in image_tag_pattern.finditer(text):
                image_ref_counter += 1
                image_ref_name = Path(match.group(1)).name

                ref_info = image_reference_info.get(image_ref_name)
                if ref_info is None:
                    image_reference_info[image_ref_name] = {
                        "first_index": image_ref_counter,
                        "first_char": match.start(),
                        "all_positions": [match.start()],
                    }
                else:
                    ref_info["all_positions"].append(match.start())
            
            image_dicts = []
            
            # Extract images from marker output
            if rendered.images:
                for img_name, img in rendered.images.items():
                    # Convert PIL image to bytes
                    img_byte_arr = BytesIO()
                    
                    # Detect format from img object or filename
                    img_format = img.format
                    if not img_format:
                        ext = Path(img_name).suffix.lower()
                        if ext in ['.jpg', '.jpeg']:
                            img_format = 'JPEG'
                        elif ext == '.png':
                            img_format = 'PNG'
                        else:
                            img_format = 'JPEG' # Default fallback
                            
                    img.save(img_byte_arr, format=img_format)
                    img_bytes = img_byte_arr.getvalue()

                    # Create image doc dict
                    img_dict = {
                        "source_id": parent_data.get("source_id", "local"),
                        "doc_id": parent_data.get("doc_id", self.file_path.stem)+"-"+img_name,
                        "doc_type": "image",
                        "source_type": parent_data.get("source_type"),
                        "binary": img_bytes,
                        "parent_id": parent_data.get("id"),
                        "meta": {
                            "image_name": img_name,
                            "parent_doc_id": parent_data.get("id", parent_data.get("doc_id")),
                            "parser": "marker",
                        }
                    }
                    
                    # Merge remaining metadata from parent
                    if "meta" in parent_data:
                        img_dict["meta"].update(parent_data["meta"])
                    
                    # Try to extract page number from name like '_page_3_Figure_3.jpeg'
                    page_match = re.search(r'page_(\d+)', img_name)
                    if page_match:
                        img_dict["meta"]["page"] = int(page_match.group(1))
                    
                    # Extract image number if possible
                    image_num_match = re.search(r'Figure_(\d+)', img_name)
                    if image_num_match:
                        img_dict["meta"]["image_number"] = int(image_num_match.group(1))

                    # Add reference position metadata from markdown, if found.
                    image_ref = image_reference_info.get(Path(img_name).name)
                    if image_ref:
                        img_dict["meta"]["image_ref_index"] = image_ref["first_index"]
                        img_dict["meta"]["image_ref_char_start"] = image_ref["first_char"]
                        img_dict["meta"]["image_ref_count"] = len(image_ref["all_positions"])
                    
                    image_dicts.append(img_dict)
                    
            return text, image_dicts
            
        except Exception as e:
            logger.error(f"Error parsing with marker: {e}", exc_info=True)
            return "", []

