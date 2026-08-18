"""Document parsers for extracting text and metadata from various formats.

Supports PDF, DOCX, PPTX, Excel, HTML, and plain text. PDF/PPTX/DOCX/HTML
default to Docling (parser_docling.py); the bespoke parsers stay available
via parser_name="legacy".
"""

from .parser_base import BaseParser
from .parser_pdf import PDFParser
from .parser_pptx import PPTXParser
from .parser_docx import DOCXParser
from .parser_excel import ExcelParser
from .parser_text import TextParser

# Import main parse function and utilities
from .parse import parse
from .utils import detect_mime_type, get_parser, PARSER_MAP
from .image_utils import display_image


__all__ = [
    'parse',
    'detect_mime_type',
    'display_image',
]

