"""Docling parser for PDF to markdown conversion."""

import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .parser_base import BaseParser

logger = logging.getLogger(__name__)

_CONVERTER_CACHE = None


def _get_converter() -> Any:
    global _CONVERTER_CACHE
    if _CONVERTER_CACHE is not None:
        return _CONVERTER_CACHE

    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except ImportError:
        logger.error("docling not installed. Install with: pip install docling")
        return None

    # Honour cache dirs set by nersc_setup_docling.sh (must be set before torch/hf imports)
    if "DOCLING_CACHE_DIR" in os.environ:
        os.makedirs(os.environ["DOCLING_CACHE_DIR"], exist_ok=True)

    logger.info("Initializing Docling DocumentConverter (this may take a while)...")

    try:
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.generate_picture_images = True

        _CONVERTER_CACHE = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
        )
        return _CONVERTER_CACHE
    except Exception as e:
        logger.error(f"Failed to initialize Docling converter: {e}")
        return None


class DoclingParser(BaseParser):
    """Parser for PDF documents using Docling."""

    def extract_text(self) -> str:
        text, _ = self.extract_text_and_images_dict({})
        return text

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        converter = _get_converter()
        if converter is None:
            return "", []

        try:
            result = converter.convert(str(self.file_path))
            doc = result.document

            text = doc.export_to_markdown()

            image_dicts = []
            image_ref_counter = 0

            # Track markdown image reference positions
            image_reference_info: Dict[str, dict] = {}
            image_tag_pattern = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
            for match in image_tag_pattern.finditer(text):
                image_ref_counter += 1
                ref_name = Path(match.group(1)).name
                ref_info = image_reference_info.get(ref_name)
                if ref_info is None:
                    image_reference_info[ref_name] = {
                        "first_index": image_ref_counter,
                        "first_char": match.start(),
                        "all_positions": [match.start()],
                    }
                else:
                    ref_info["all_positions"].append(match.start())

            # Extract pictures from docling document
            for pic_idx, pic in enumerate(doc.pictures, start=1):
                try:
                    pil_img = pic.image.pil_image if pic.image else None
                    if pil_img is None:
                        continue

                    img_byte_arr = BytesIO()
                    img_format = pil_img.format or "PNG"
                    pil_img.save(img_byte_arr, format=img_format)
                    img_bytes = img_byte_arr.getvalue()

                    # Use picture reference id or synthesise a stable name
                    img_name = getattr(pic, "self_ref", None) or f"picture_{pic_idx}"
                    img_name = str(img_name).lstrip("#/").replace("/", "_")

                    page_no: Optional[int] = None
                    if pic.prov:
                        page_no = pic.prov[0].page_no

                    img_dict = {
                        "source_id": parent_data.get("source_id", "local"),
                        "doc_id": parent_data.get("doc_id", self.file_path.stem) + "-" + img_name,
                        "doc_type": "image",
                        "source_type": parent_data.get("source_type"),
                        "binary": img_bytes,
                        "parent_id": parent_data.get("id"),
                        "meta": {
                            "image_name": img_name,
                            "image_number": pic_idx,
                            "parent_doc_id": parent_data.get("id", parent_data.get("doc_id")),
                            "parser": "docling",
                        },
                    }

                    if page_no is not None:
                        img_dict["meta"]["page"] = page_no

                    if "meta" in parent_data:
                        img_dict["meta"].update(parent_data["meta"])

                    image_ref = image_reference_info.get(img_name)
                    if image_ref:
                        img_dict["meta"]["image_ref_index"] = image_ref["first_index"]
                        img_dict["meta"]["image_ref_char_start"] = image_ref["first_char"]
                        img_dict["meta"]["image_ref_count"] = len(image_ref["all_positions"])

                    image_dicts.append(img_dict)

                except Exception as e:
                    logger.warning(f"Failed to extract picture {pic_idx}: {e}")
                    continue

            return text, image_dicts

        except Exception as e:
            logger.error(f"Error parsing with Docling: {e}", exc_info=True)
            return "", []