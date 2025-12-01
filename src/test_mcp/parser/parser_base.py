"""Base parser class for document parsers.

Based on the parser structure from mu2eDocChat:
https://github.com/corrodis/mu2eDocChat
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from .text_utils import clean_text

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """Base class for document parsers."""

    def __init__(self, file_path, doc_type):
        """Initialize parser.
        
        Args:
            file_path: Path to the document file
            doc_type: Document type (MIME type or extension)
        """
        self.file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
        self.doc_type = doc_type

    @abstractmethod
    def extract_text(self) -> str:
        """Extract raw text from the document.
        
        This should be implemented by subclasses to extract raw text.
        The text will be cleaned by get_text() automatically.
        
        Returns:
            Raw extracted text content
        """
        pass

    def get_text(self) -> str:
        """Get cleaned and processed text from the document.
        
        This method calls extract_text() and applies text cleaning.
        Override this method if you need document-specific processing
        (e.g., slide formatting, page tags, etc.).
        
        Returns:
            Cleaned and processed text content
        """
        text = self.extract_text()
        # Apply standard text cleaning
        return clean_text(text)

    def get_metadata(self) -> Optional[Dict[str, Any]]:
        """Get additional metadata from the document.
        
        Override this method in subclasses to provide document-specific metadata.
        
        Returns:
            Dictionary with metadata, or None if no additional metadata available
        """
        return None

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract text and images from document, returning image dictionaries.
        
        This is an optional method that parsers can override to support image extraction.
        By default, it just returns the text and an empty list of images.
        
        Args:
            parent_data: Dictionary with parent document data (source_id, doc_id, etc.)
        
        Returns:
            Tuple of (text_with_placeholders, list_of_image_dicts)
            
            - text: Extracted text with [Image X] placeholders inserted where images were found.
                    Placeholders should be in format "[Image {image_number}]" or "\n\n[Image {image_number}]\n\n"
            
            - list_of_image_dicts: List of image dictionaries. Each dict must have:
                - doc_id: str - Unique identifier (e.g., "{parent_data['doc_id']}-image-{image_number}")
                - source_id: str - From parent_data["source_id"]
                - source_type: str - MIME type (e.g., "image/png")
                - doc_type: str - Must be "image"
                - binary: bytes - Image binary data (should be resized if needed, e.g., max 500px)
                - meta: dict - Must include:
                    - page: int - Page number where image was found (1-indexed)
                    - image_number: int - Sequential image number within document
                    - May include additional metadata merged from parent_data.get("meta", {})
        
        Note: The image dicts returned here are complete and ready to be used as Document objects.
        The parse() function will filter these based on create_additional_docs flag.
        """
        # Default implementation: just return text, no images
        return self.get_text(), []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(file_path={self.file_path}, doc_type={self.doc_type})>"

