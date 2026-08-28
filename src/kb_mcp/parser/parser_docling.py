"""Docling parser for high-quality PDF to markdown conversion.

IBM Research's Docling library — DocLayNet layout detection plus TableFormer for
table structure. Produces structured Markdown with preserved headings, lists,
and tables. Mirrors the lazy-init / module-level cache pattern of `parser_marker.py`.
"""

import html
import io
import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .parse import DOCLING_PAGE_BREAK_PLACEHOLDER, number_docling_page_breaks
from .parser_base import BaseParser

logger = logging.getLogger(__name__)

# Module-level converter cache, keyed on whether formula enrichment is on.
# Two converters at most: { False: <plain>, True: <with_formula_enrichment> }.
# OCR (PARSE_OCR) is read from env config at init time and is constant for
# the process lifetime, so it doesn't need to participate in the cache key.
# Per-document dispatch (PARSE_FORMULA_ENRICHMENT_AUTO)
# decides which one to use; both share the same format_options for non-PDF
# inputs, so PPTX / DOCX / HTML / XLSX outputs are identical regardless.
_CONVERTER_CACHE: dict = {}


#: What Docling's Markdown export writes for a formula whose `text` is empty.
_FORMULA_PLACEHOLDER = "<!-- formula-not-decoded -->"


def _fill_undecoded_formulas(text: str, structured_output: Optional[dict]) -> str:
    """Replace `<!-- formula-not-decoded -->` markers with the raw formula text.

    A formula item Docling could not decode keeps its `text` empty but still
    carries the layout model's raw reading in `orig`
    (``"R µe = Γ( µ - + N ( A,Z ) → e - ...)"``). The Markdown export only
    looks at `text`, so that reading is dropped and the equation reaches the
    index as a comment carrying no signal at all.

    `orig` is not LaTeX and is not as good as running the formula model, but
    the symbols in it are what someone searching for the equation will type.
    Falling back to it beats indexing a placeholder — and enrichment being off
    is the default for documents whose math density doesn't justify the cost.

    Markers are consumed in body order against the formula items, matching how
    `inline_docling_image_descriptions` pairs picture markers with pictures.
    """
    if _FORMULA_PLACEHOLDER not in text or not structured_output:
        return text

    texts_by_ref = {
        t.get("self_ref"): t for t in (structured_output.get("texts") or [])
    }
    body_children = (structured_output.get("body") or {}).get("children") or []
    formula_refs = [
        child.get("cref") for child in body_children
        if isinstance(child, dict)
        and (texts_by_ref.get(child.get("cref")) or {}).get("label") == "formula"
    ]
    marker_iter = iter(formula_refs)

    def _replace(match):
        ref = next(marker_iter, None)
        if ref is None:
            return match.group(0)
        orig = ((texts_by_ref.get(ref) or {}).get("orig") or "").strip()
        # Collapse the layout model's ragged spacing; keep it inline so the
        # surrounding sentence still reads as one passage.
        orig = " ".join(orig.split())
        return orig or match.group(0)

    return re.sub(re.escape(_FORMULA_PLACEHOLDER), _replace, text)


def _get_converter(with_formula_enrichment: bool = False) -> Any:
    """Initialize and return a docling DocumentConverter (cached).

    The converter is registered for all formats kb-mcp routes through
    Docling — PDF (custom layout-detection + table-structure pipeline,
    accelerator-aware), plus PPTX / DOCX / HTML / XLSX with Docling's
    default pipelines. The non-PDF readers don't need an accelerator;
    cold-start is cheap.

    Two cached variants:
      * `with_formula_enrichment=False` (default): fast PDF pipeline,
        formulas left as `<!-- formula-not-decoded -->` placeholders.
      * `with_formula_enrichment=True`: PDF pipeline runs the
        `CodeFormulaV2` model on each page. Recovers `$$...$$` LaTeX
        blocks at ~+78 % parse time on equation-heavy documents.

    Raises:
        ImportError: docling is not installed.
        RuntimeError: the converter could not be constructed.
    """
    if with_formula_enrichment in _CONVERTER_CACHE:
        return _CONVERTER_CACHE[with_formula_enrichment]

    try:
        from docling.document_converter import (
            DocumentConverter,
            PdfFormatOption,
            PowerpointFormatOption,
            WordFormatOption,
            HTMLFormatOption,
            ExcelFormatOption,
        )
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            AcceleratorDevice,
            AcceleratorOptions,
            PdfPipelineOptions,
        )
    except ImportError as e:
        # Raise rather than degrade: a missing backend used to surface as an
        # empty parse, which the ingest pipeline happily stored as a
        # zero-length document and reported as a successful import.
        raise ImportError(
            "docling is not installed, but it is the default parser for "
            "PDF/PPTX/DOCX/HTML/XLSX. Install the extra: "
            'pip install -e ".[docling]"'
        ) from e

    # Honour cache dirs set by nersc_setup_docling.sh (must be set before torch/hf imports)
    if "DOCLING_CACHE_DIR" in os.environ:
        os.makedirs(os.environ["DOCLING_CACHE_DIR"], exist_ok=True)

    from ..config import get_parser_config
    do_ocr = bool(get_parser_config().get("ocr", True))

    logger.info(
        "Initializing docling DocumentConverter "
        f"(ocr={do_ocr}, formula_enrichment={with_formula_enrichment}; "
        "model download on first use)..."
    )

    try:
        device = AcceleratorDevice.CUDA if _cuda_available() else AcceleratorDevice.CPU
        pdf_pipeline_options = PdfPipelineOptions()
        pdf_pipeline_options.do_ocr = do_ocr
        pdf_pipeline_options.do_table_structure = True
        pdf_pipeline_options.generate_picture_images = True
        if with_formula_enrichment:
            pdf_pipeline_options.do_formula_enrichment = True
            logger.info("docling formula enrichment ENABLED (CodeFormulaV2)")
        pdf_pipeline_options.accelerator_options = AcceleratorOptions(device=device)
        logger.info(f"docling accelerator device: {device.value}")

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_pipeline_options),
                InputFormat.PPTX: PowerpointFormatOption(),
                InputFormat.DOCX: WordFormatOption(),
                InputFormat.HTML: HTMLFormatOption(),
                InputFormat.XLSX: ExcelFormatOption(),
            }
        )
        _CONVERTER_CACHE[with_formula_enrichment] = converter
        return converter
    except Exception as e:
        raise RuntimeError(f"Failed to initialize docling: {e}") from e


def _cuda_available() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _build_nearby_text_index(docling_dict: dict, window: int = 2) -> dict:
    """Index `cref → nearby_text` for tables and pictures by body order.

    Walks `docling_dict["body"]["children"]` (top-level), keeping a sliding
    window of the most recent text-element strings. When a table or picture
    cref appears in the walk, its `nearby_text` is the joined window — the
    paragraph(s) immediately preceding the figure/table in reading order.

    Used to give figures and tables retrievable surrounding context when
    Docling didn't associate an explicit caption (which is most figures in
    practice). Group children (e.g. list items) are skipped in this v1.

    Args:
        docling_dict: Persisted DoclingDocument JSON.
        window: How many preceding text elements to include.

    Returns:
        Dict keyed by self_ref ("#/tables/0", "#/pictures/3", ...) with the
        joined preceding-text string (or "" if nothing precedes).
    """
    body = (docling_dict.get("body") or {})
    body_children = body.get("children") or []
    texts_by_ref: dict = {}
    for i, t in enumerate(docling_dict.get("texts") or []):
        sr = t.get("self_ref") or f"#/texts/{i}"
        texts_by_ref[sr] = t

    nearby: dict = {}
    recent: list = []
    for child in body_children:
        cref = child.get("cref") if isinstance(child, dict) else None
        if not cref:
            continue
        if cref.startswith("#/texts/"):
            t = texts_by_ref.get(cref) or {}
            txt = (t.get("text") or "").strip()
            if txt:
                recent.append(txt)
                if len(recent) > window:
                    recent = recent[-window:]
        elif cref.startswith("#/tables/") or cref.startswith("#/pictures/"):
            nearby[cref] = "\n\n".join(recent)
        # groups, key_value_items, form_items, etc. are skipped for v1
    return nearby


class DoclingParser(BaseParser):
    """Parser for PDF documents using IBM Docling."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populated by extract_text_and_images_dict — `parse()` reads this
        # generic hook to persist the canonical DoclingDocument JSON into
        # documents.parser_output alongside the document.
        self.structured_output: dict | None = None
        # Tables-as-records: each entry is a Document-shaped dict with
        # doc_type="table" carrying the rendered Markdown table + provenance
        # metadata. Walked into the returned doc_dicts by `parse()`.
        self.table_dicts: list[dict] = []

    def extract_text(self) -> str:
        text, _ = self.extract_text_and_images_dict({})
        return text

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract Markdown text and images from PDF using docling.

        Args:
            parent_data: Dictionary with parent document data (source_id, doc_id, ...).

        Returns:
            Tuple of (markdown_text, list_of_image_dicts).
        """
        # Decide whether to enable formula enrichment for this single
        # document. Three modes via env vars:
        #   * PARSE_FORMULA_ENRICHMENT=true   → always on (manual)
        #   * PARSE_FORMULA_ENRICHMENT_AUTO=true → math-density pre-scan
        #     decides per-document; threshold from
        #     PARSE_FORMULA_ENRICHMENT_AUTO_THRESHOLD (default 0.005).
        #   * neither set                     → off (fastest).
        # Auto wins over manual when both are set, since auto's "off
        # decision" is the careful one (the doc has no math).
        enable_formula = False
        try:
            from ..config import get_parser_config
            cfg = get_parser_config()
            # `doc_type`, not `mime_type`: ParserBase stores the MIME type it
            # was constructed with under that name. Reading a non-existent
            # attribute here raised AttributeError into the except below,
            # which silently left enrichment off — so the auto path never
            # once fired, and it masked the manual flag too (the `elif` is
            # unreachable once the `if` raises).
            if cfg.get("formula_enrichment_auto", False) and self.doc_type == "application/pdf":
                from .math_density import should_enable_formula_enrichment
                threshold = cfg.get("formula_enrichment_auto_threshold", 0.005)
                enable_formula, density = should_enable_formula_enrichment(
                    self.file_path, threshold=threshold,
                )
                logger.info(
                    f"docling auto-decide: math_density={density:.4f} "
                    f"threshold={threshold:.4f} → "
                    f"formula_enrichment={'ON' if enable_formula else 'OFF'} "
                    f"({self.file_path.name})"
                )
            elif cfg.get("formula_enrichment", False):
                enable_formula = True
        except Exception as cfg_err:
            # Warning, not debug: this path silently disables a feature the
            # operator explicitly switched on, and at debug level that hid a
            # plain AttributeError indefinitely.
            logger.warning(
                "formula-enrichment dispatch failed (%s: %s); parsing without it",
                type(cfg_err).__name__, cfg_err,
            )

        converter = _get_converter(with_formula_enrichment=enable_formula)

        try:
            result = converter.convert(str(self.file_path))
            doc = result.document

            # Capture the structured DoclingDocument as a JSON-serialisable
            # dict before exporting to Markdown. Stored on `self` so `parse()`
            # can persist it as the canonical parser artifact. Picture bytes
            # are stripped here — they're 95–97% of the dict size and already
            # extracted as separate `doc_type="image"` records below; the
            # remaining picture metadata (caption, prov bbox, refs) is what
            # matters for retrieval.
            try:
                self.structured_output = doc.model_dump(mode="json")
                for pic in self.structured_output.get("pictures", []) or []:
                    pic.pop("image", None)
            except Exception as dump_err:
                logger.warning(f"docling: model_dump failed, JSON not persisted: {dump_err}")
                self.structured_output = None

            # Docling escapes inequalities and a few other characters in its
            # Markdown output (e.g. `Rate &lt; 20 kcps`). Unescape so chunks
            # carry the original text.
            text = html.unescape(doc.export_to_markdown(
                page_break_placeholder=DOCLING_PAGE_BREAK_PLACEHOLDER
            ))
            text = _fill_undecoded_formulas(text, self.structured_output)
            text = number_docling_page_breaks(text, self.structured_output)

            # Tables-as-records. Walk doc.tables, render each
            # as a Markdown table (caption + grid), and emit a Document-shaped
            # dict with doc_type="table". The chunker + embedder then index
            # these the same way they index parent documents, but search code
            # can boost / filter on doc_type="table" for table-shaped queries.
            nearby_index = _build_nearby_text_index(self.structured_output or {})

            self.table_dicts = []
            tables = getattr(doc, "tables", None) or []
            for idx, table in enumerate(tables):
                try:
                    page_no = None
                    bbox = None
                    prov = getattr(table, "prov", None) or []
                    if prov:
                        page_no = getattr(prov[0], "page_no", None)
                        prov_bbox = getattr(prov[0], "bbox", None)
                        if prov_bbox is not None and hasattr(prov_bbox, "model_dump"):
                            # model_dump(mode="json") converts the CoordOrigin
                            # enum to its string value so the dict is
                            # JSON-serialisable for the documents.meta JSONB.
                            bbox = prov_bbox.model_dump(mode="json")

                    caption = ""
                    try:
                        caption = (table.caption_text(doc) or "").strip()
                    except Exception:
                        caption = ""

                    try:
                        table_md = html.unescape(table.export_to_markdown(doc) or "")
                    except Exception as md_err:
                        logger.warning(f"docling: failed to render table {idx} as markdown: {md_err}")
                        table_md = ""

                    self_ref = getattr(table, "self_ref", None)
                    nearby_text = nearby_index.get(self_ref, "") if self_ref else ""

                    # Order: surrounding paragraph context → caption → grid.
                    # Putting nearby_text first means search/embedding sees the
                    # surrounding-paragraph signal even when caption is empty
                    # (which is most tables in our sample).
                    text_parts = []
                    if nearby_text:
                        text_parts.append(nearby_text)
                    if caption:
                        text_parts.append(caption)
                    if table_md:
                        text_parts.append(table_md)
                    table_text = "\n\n".join(text_parts).strip()
                    if not table_text:
                        # Nothing useful — skip empty tables
                        continue

                    data = getattr(table, "data", None)
                    num_rows = getattr(data, "num_rows", None) if data else None
                    num_cols = getattr(data, "num_cols", None) if data else None

                    table_dict = {
                        "source_id": parent_data.get("source_id", "local"),
                        "doc_id": parent_data.get("doc_id", self.file_path.stem) + f"-table-{idx}",
                        "doc_type": "table",
                        "source_type": "text/markdown",
                        "text": table_text,
                        "parent_id": parent_data.get("id"),
                        "uri": parent_data.get("uri"),
                        "meta": {
                            "table_index": idx,
                            "page": page_no,
                            "bbox": bbox,
                            "caption": caption or None,
                            "nearby_text": nearby_text or None,
                            "num_rows": num_rows,
                            "num_cols": num_cols,
                            "self_ref": self_ref,
                            "parser": "docling",
                            # See section_dict — `add_many()` resolves
                            # this to a real parent_id during ingest.
                            "parent_doc_id": parent_data.get("doc_id"),
                        },
                    }
                    if "meta" in parent_data:
                        # Carry parent-level meta (filename, source_type, etc.)
                        # — table-specific keys above take precedence.
                        merged = dict(parent_data["meta"])
                        merged.update(table_dict["meta"])
                        table_dict["meta"] = merged

                    self.table_dicts.append(table_dict)
                except Exception as t_err:
                    logger.warning(f"docling: failed to extract table {idx}: {t_err}")
                    continue

            image_dicts = []
            pictures = getattr(doc, "pictures", None) or []
            for idx, picture in enumerate(pictures):
                try:
                    pil_image = picture.get_image(doc)
                    if pil_image is None:
                        continue

                    img_byte_arr = io.BytesIO()
                    img_format = pil_image.format or "PNG"
                    pil_image.save(img_byte_arr, format=img_format)
                    img_bytes = img_byte_arr.getvalue()

                    page_no = None
                    bbox = None
                    prov = getattr(picture, "prov", None) or []
                    if prov:
                        page_no = getattr(prov[0], "page_no", None)
                        prov_bbox = getattr(prov[0], "bbox", None)
                        if prov_bbox is not None and hasattr(prov_bbox, "model_dump"):
                            bbox = prov_bbox.model_dump(mode="json")

                    # Caption text — Docling stores caption text refs on
                    # picture.captions; resolve them via caption_text(doc).
                    caption = ""
                    try:
                        caption = (picture.caption_text(doc) or "").strip()
                    except Exception:
                        caption = ""

                    img_name = f"_page_{page_no if page_no is not None else idx}_Figure_{idx}.png"

                    self_ref = getattr(picture, "self_ref", None)
                    nearby_text = nearby_index.get(self_ref, "") if self_ref else ""

                    img_dict = {
                        "source_id": parent_data.get("source_id", "local"),
                        "doc_id": parent_data.get("doc_id", self.file_path.stem) + "-" + img_name,
                        "doc_type": "image",
                        "source_type": parent_data.get("source_type"),
                        "binary": img_bytes,
                        "parent_id": parent_data.get("id"),
                        "uri": parent_data.get("uri"),
                        "meta": {
                            "image_name": img_name,
                            "image_number": idx,
                            "page": page_no,
                            "bbox": bbox,
                            "caption": caption or None,
                            "nearby_text": nearby_text or None,
                            "self_ref": self_ref,
                            "parser": "docling",
                            # See section_dict — `add_many()` resolves
                            # this to a real parent_id during ingest.
                            "parent_doc_id": parent_data.get("doc_id"),
                        },
                    }
                    # Seed `text` with surrounding context + caption so search
                    # hits even when no VLM description has been generated.
                    # The LLM description path
                    # (`image_descriptions.generate_image_descriptions()`) is
                    # caption-aware and will combine caption+description; the
                    # nearby_text remains visible only via meta in that case.
                    seed_parts = [p for p in (nearby_text, caption) if p]
                    if seed_parts:
                        img_dict["text"] = "\n\n".join(seed_parts)
                    if "meta" in parent_data:
                        img_dict["meta"].update(parent_data["meta"])

                    image_dicts.append(img_dict)
                except Exception as img_err:
                    logger.warning(f"docling: failed to extract picture {idx}: {img_err}")
                    continue

            return text, image_dicts

        except Exception as e:
            # Same reasoning as the import failure above: returning empty text
            # here would be recorded as a successfully parsed, empty document.
            logger.error(f"Error parsing with docling: {e}", exc_info=True)
            raise
