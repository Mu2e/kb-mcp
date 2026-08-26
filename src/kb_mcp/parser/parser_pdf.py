"""PDF parser for extracting text from PDF files."""

import base64
import io
import logging
import os
from pathlib import Path
from typing import List, Tuple, Optional

from .parser_base import BaseParser
from .text_utils import slides_format_as_markdown, clean_text
from .image_utils import resize_image

logger = logging.getLogger(__name__)

# Import Document type for type hints (avoid circular import)
try:
    from ...kb.db_models import Document
    HAS_KB = True
except ImportError:
    HAS_KB = False
    Document = None


class PDFParser(BaseParser):
    """Parser for PDF documents."""

    def extract_text(self) -> str:
        """Extract raw text from PDF file."""
        try:
            import PyPDF2
        except ImportError:
            logger.warning(
                "PyPDF2 not installed. Install with: pip install PyPDF2"
            )
            return ""

        try:
            text_parts = []
            with open(self.file_path, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                for page in pdf_reader.pages:
                    text_parts.append(page.extract_text())
            return "\n\n".join(text_parts)
        except Exception as e:
            logger.error(f"Error extracting text from PDF {self.file_path}: {e}")
            return ""


    def _is_slide_like(self) -> bool:
        """Detect if PDF appears to be slide-like based on metadata.
        
        Checks PDF metadata (creator/producer/title) for indicators that
        the PDF was created from a presentation tool.
        
        Returns:
            True if PDF metadata indicates it's from a presentation tool
        """
        try:
            import PyPDF2
            with open(self.file_path, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                metadata = pdf_reader.metadata
                
                if metadata:
                    creator = str(metadata.get('/Creator', '')).lower()
                    producer = str(metadata.get('/Producer', '')).lower()
                    title = str(metadata.get('/Title', '')).lower()
                    
                    # Check for PowerPoint or other presentation tools
                    slide_indicators = [
                        'powerpoint', 'microsoft powerpoint', 'ppt', 'pptx',
                        'keynote', 'google slides', 'libreoffice impress',
                        'presentation', 'slide'
                    ]
                    
                    for indicator in slide_indicators:
                        if indicator in creator or indicator in producer or indicator in title:
                            logger.debug(f"PDF metadata indicates slides: creator={creator}, producer={producer}")
                            return True
        except Exception as e:
            logger.debug(f"Error reading PDF metadata: {e}")
        
        return False

    def get_text(self) -> str:
        """Get formatted text from PDF.
        
        If PDF is slide-like (based on metadata), applies markdown formatting.
        For image extraction, use extract_text_and_images_dict() instead.
        """
        text = self.extract_text()
        
        # Check if this appears to be a slide presentation (based on metadata)
        if self._is_slide_like():
            logger.info("Detected slide-like PDF from metadata, applying markdown formatting")
            # Apply slide-specific formatting
            text = slides_format_as_markdown(text)
        
        # Apply standard cleaning
        return clean_text(text)

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract text and images from PDF, returning image dictionaries.
        
        This method extracts text and images, inserting [Image X on page Y] placeholders in the text.
        Image descriptions are NOT generated here - that's done separately in parse().
        
        Always creates minimal image dicts with meta (page, image_number, image_base64) for description generation.
        Full fields (source_id, doc_id, binary, etc.) are added in parse() if create_additional_docs is enabled.
        
        Args:
            parent_data: Dictionary with parent document data
        
        Returns:
            Tuple of (text_with_placeholders, list_of_image_dicts)
            - text: Extracted text with [Image X on page Y] placeholders
            - list_of_image_dicts: List of minimal image dicts with meta (for descriptions)
        """
        
        # If we get here, at least one feature is enabled, so we'll extract images
        
        try:
            import PyPDF2
            from PIL import Image as PILImage
        except ImportError:
            logger.debug("PyPDF2 or PIL not available for image extraction")
            return self.get_text(), []

        try:
            text_parts = []
            image_dicts = []
            image_counter = 0
            
            with open(self.file_path, "rb") as f:
                pdf_reader = PyPDF2.PdfReader(f)
                
                for page_num, page in enumerate(pdf_reader.pages):
                    # Extract text
                    page_text = page.extract_text()
                    page_num_1_indexed = page_num + 1
                    
                    # Extract images from page and collect placeholders
                    page_image_placeholders = []
                    try:
                        if '/Resources' in page and '/XObject' in page['/Resources']:
                            xobjects = page['/Resources']['/XObject']
                            
                            # Handle both direct dict and indirect reference
                            if hasattr(xobjects, 'get_object'):
                                xobjects = xobjects.get_object()
                            
                            for obj_name in xobjects:
                                obj = xobjects[obj_name]
                                
                                # Handle indirect references
                                if hasattr(obj, 'get_object'):
                                    obj = obj.get_object()
                                
                                if obj.get('/Subtype') == '/Image':
                                    try:
                                        # Get image data
                                        if hasattr(obj, '_data'):
                                            image_data = obj._data
                                        elif hasattr(obj, 'get_data'):
                                            image_data = obj.get_data()
                                        else:
                                            logger.debug(f"Could not extract image data from object {obj_name}")
                                            continue
                                        
                                        # Open image with PIL
                                        img = PILImage.open(io.BytesIO(image_data))
                                        
                                        # Resize if needed
                                        img = resize_image(img, max_dim=500)
                                        
                                        # Convert to bytes for binary storage
                                        buffered = io.BytesIO()
                                        img.save(buffered, format='PNG')
                                        image_bytes = buffered.getvalue()
                                        
                                        image_counter += 1
                                        
                                        # Convert to base64 for description generation
                                        #image_base64 = base64.b64encode(image_bytes).decode()
                                        
                                        # Create placeholder text
                                        image_name = f"image-{image_counter}.png"
                                        placeholder = f"\n\n![Image {image_counter}]({image_name})\n\n"
                                        page_image_placeholders.append(placeholder)
                                        
                                        # Always create minimal image dict with meta (for description generation)
                                        # Full fields (source_id, doc_id, binary, etc.) will be added in parse() if needed
                                        image_dict = {
                                            "doc_id": f"{parent_data['doc_id']}-{image_name}",
                                            "source_id": parent_data["source_id"],
                                            "source_type": "image/png",
                                            "doc_type": "image",
                                            "binary": image_bytes,
                                            "uri": parent_data.get("uri"),
                                            "meta": {
                                                "page": page_num_1_indexed,
                                                "image_number": image_counter,
                                                "image_name": image_name,
                                                "parent_doc_id": parent_data.get("id", parent_data['doc_id']),
                                            }
                                        }
                                        image_dict["meta"] = image_dict["meta"] | parent_data.get("meta", {})

                                        image_dicts.append(image_dict)
                                        
                                    except Exception as e:
                                        logger.debug(f"Error extracting image from PDF page {page_num}, object {obj_name}: {e}")
                                        continue
                    except Exception as e:
                        logger.debug(f"Error accessing page resources for page {page_num}: {e}")
                        continue
                    
                    # Append page text with image placeholders
                    page_text_with_images = page_text + "".join(page_image_placeholders)
                    text_parts.append(page_text_with_images)
            
            # Combine text parts (includes placeholders)
            text = "\n\n".join(text_parts)
            
            # Apply slide formatting if needed
            if self._is_slide_like():
                logger.info("Detected slide-like PDF from metadata, applying markdown formatting")
                text = slides_format_as_markdown(text)
            
            # Apply standard cleaning
            text = clean_text(text)
            
            return text, image_dicts
            
        except Exception as e:
            logger.error(f"Error extracting images from PDF {self.file_path}: {e}")
            # Fall back to text-only extraction
            return self.get_text(), []




