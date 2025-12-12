"""Word document parser for extracting text from DOCX files."""

import logging
from pathlib import Path

from .parser_base import BaseParser

logger = logging.getLogger(__name__)


class DOCXParser(BaseParser):
    """Parser for Word documents (DOCX and DOC)."""

    def extract_text(self) -> str:
        """Extract text from Word document."""
        try:
            import docx
        except ImportError:
            logger.warning(
                "python-docx not installed. Install with: pip install python-docx"
            )
            return ""

        try:
            doc = docx.Document(self.file_path)
            text_parts = []
            for paragraph in doc.paragraphs:
                text_parts.append(paragraph.text)
            return "\n".join(text_parts)
        except Exception as e:
            logger.error(f"Error extracting text from Word doc {self.file_path}: {e}")
            return ""

