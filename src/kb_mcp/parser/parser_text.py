"""Text parser for extracting text from plain text, HTML, and other text files."""

import logging
from pathlib import Path

from .parser_base import BaseParser

logger = logging.getLogger(__name__)


class TextParser(BaseParser):
    """Parser for plain text, HTML, Markdown, and other text-based files."""

    def extract_text(self) -> str:
        """Extract text from text file."""
        mime_type = self.doc_type.lower()
        
        # HTML files - extract text content
        if mime_type in ("text/html", "application/xhtml+xml"):
            return self._extract_html()
        
        # Plain text files - read directly
        try:
            return self.file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            logger.error(f"Error reading text file {self.file_path}: {e}")
            return ""

    def _extract_html(self) -> str:
        """Extract text from HTML file."""
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.warning(
                "BeautifulSoup4 not installed. Install with: pip install beautifulsoup4"
            )
            # Fallback: read as text
            return self.file_path.read_text(encoding="utf-8", errors="ignore")

        try:
            html_content = self.file_path.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(html_content, "html.parser")
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            # Get text
            text = soup.get_text()
            # Clean up whitespace
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = "\n".join(chunk for chunk in chunks if chunk)
            return text
        except Exception as e:
            logger.error(f"Error extracting text from HTML {self.file_path}: {e}")
            return self.file_path.read_text(encoding="utf-8", errors="ignore")

