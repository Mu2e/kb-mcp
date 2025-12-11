"""
Document parsers module - extract text and metadata from various document formats.

This module is based on the parser structure from mu2eDocChat:
https://github.com/corrodis/mu2eDocChat
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

