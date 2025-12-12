"""Utility functions for document parsing."""

from pathlib import Path
from typing import Optional

from .parser_base import BaseParser
from .parser_pdf import PDFParser
from .parser_pptx import PPTXParser
from .parser_docx import DOCXParser
from .parser_excel import ExcelParser
from .parser_text import TextParser

# Parser mapping - maps MIME type or file extension to parser class
PARSER_MAP = {
    # PDF
    'pdf': PDFParser,
    'application/pdf': PDFParser,
    # PowerPoint
    'pptx': PPTXParser,
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': PPTXParser,
    # Word
    'docx': DOCXParser,
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': DOCXParser,
    'application/msword': DOCXParser,
    'doc': DOCXParser,
    # Excel
    'xlsx': ExcelParser,
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ExcelParser,
    # Text
    'txt': TextParser,
    'text/plain': TextParser,
    'text/html': TextParser,
    'text/markdown': TextParser,
    'text/xml': TextParser,
    'application/xhtml+xml': TextParser,
}


def get_parser(file_path: str | Path, doc_type: Optional[str] = None) -> BaseParser:
    """Create appropriate parser for document type.
    
    Args:
        file_path: Path to the document file
        doc_type: Optional document type (MIME type or extension). 
                  If not provided, will be auto-detected.
    
    Returns:
        Parser instance
    
    Raises:
        NotImplementedError: If document type is not supported
    """
    file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
    
    if doc_type is None:
        # Auto-detect MIME type
        doc_type = detect_mime_type(file_path)
    
    # Normalize doc_type - try both full MIME type and extension
    doc_type_lower = doc_type.lower()
    
    # Try full MIME type first
    if doc_type_lower in PARSER_MAP:
        return PARSER_MAP[doc_type_lower](file_path, doc_type_lower)
    
    # Try extension
    if '.' in doc_type_lower:
        ext = doc_type_lower.split('.')[-1]
        if ext in PARSER_MAP:
            return PARSER_MAP[ext](file_path, ext)
    
    # Try file extension from path
    file_ext = file_path.suffix.lower().lstrip('.')
    if file_ext in PARSER_MAP:
        return PARSER_MAP[file_ext](file_path, file_ext)
    
    raise NotImplementedError(
        f"Document type '{doc_type}' not supported yet. "
        f"Available: {', '.join(PARSER_MAP.keys())}"
    )


def detect_mime_type(file_path: str | Path) -> str:
    """Detect MIME type from file path.
    
    Uses python-magic if available, otherwise falls back to mimetypes.
    
    Args:
        file_path: Path to the file
    
    Returns:
        MIME type string
    """
    file_path = Path(file_path) if not isinstance(file_path, Path) else file_path
    
    # Try python-magic first (more accurate)
    try:
        import magic
        mime = magic.Magic(mime=True)
        mime_type = mime.from_file(str(file_path))
        if mime_type:
            return mime_type
    except ImportError:
        pass
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.debug(f"Error using python-magic: {e}")
    
    # Fall back to mimetypes
    import mimetypes
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        return mime_type
    
    # Last resort: use file extension
    ext = file_path.suffix.lower().lstrip('.')
    mime_type, _ = mimetypes.guess_type(f"dummy.{ext}")
    if mime_type:
        return mime_type
    
    return "application/octet-stream"

