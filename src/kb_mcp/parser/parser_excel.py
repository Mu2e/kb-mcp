"""Excel parser for extracting text from XLSX files."""

import logging
from pathlib import Path

from .parser_base import BaseParser

logger = logging.getLogger(__name__)


class ExcelParser(BaseParser):
    """Parser for Excel spreadsheets (XLSX)."""

    def extract_text(self) -> str:
        """Extract text from Excel spreadsheet."""
        try:
            import openpyxl
        except ImportError:
            logger.warning(
                "openpyxl not installed. Install with: pip install openpyxl"
            )
            return ""

        try:
            # read_only streams rows lazily instead of building a full Cell
            # object graph (styles, comments, merged-cell refs, ...) for
            # every coordinate in the sheet's declared used range. Without
            # it, a workbook whose used range is inflated by formatting or
            # full-column defined names (e.g. print areas like '$A:$Z') can
            # balloon to tens of GB in memory even when the file itself is
            # only a few MB on disk — this is what OOM-killed the mu2e-docdb
            # parse-all pod on a family of monthly cost-report spreadsheets.
            workbook = openpyxl.load_workbook(self.file_path, read_only=True)
            text_parts = []
            for sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
                text_parts.append(f"=== Sheet: {sheet_name} ===")
                for row in sheet.iter_rows(values_only=True):
                    row_text = "\t".join(str(cell) if cell is not None else "" for cell in row)
                    if row_text.strip():
                        text_parts.append(row_text)
            return "\n".join(text_parts)
        except Exception as e:
            logger.error(f"Error extracting text from Excel {self.file_path}: {e}")
            return ""

