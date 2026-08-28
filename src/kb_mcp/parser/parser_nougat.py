"""Nougat parser for scientific PDF to Markdown conversion."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .parser_base import BaseParser

logger = logging.getLogger(__name__)

_MODEL_CACHE: Optional[Any] = None
_PROCESSOR_CACHE: Optional[Any] = None


def _get_model_and_processor() -> Tuple[Any, Any]:
    """Initialize and return the Nougat model and processor (cached).

    Raises:
        ImportError: transformers or torch is not installed.
        RuntimeError: the model or processor could not be loaded.
    """
    global _MODEL_CACHE, _PROCESSOR_CACHE
    if _MODEL_CACHE is not None and _PROCESSOR_CACHE is not None:
        return _MODEL_CACHE, _PROCESSOR_CACHE

    try:
        from transformers import AutoModelForImageTextToText, NougatProcessor
    except ImportError as e:
        # Raise rather than degrade: returning None here used to surface as an
        # empty parse, which the ingest pipeline stored as a zero-length
        # document and reported as a successful import.
        raise ImportError(
            "transformers is not installed. Install with: pip install transformers"
        ) from e

    try:
        import torch
    except ImportError as e:
        raise ImportError("torch is not installed.") from e

    model_id = "facebook/nougat-base"
    logger.info(f"Initializing Nougat model {model_id} (this may take a while)...")

    try:
        processor = NougatProcessor.from_pretrained(model_id)
        model = AutoModelForImageTextToText.from_pretrained(model_id, dtype=torch.bfloat16)

        if torch.cuda.is_available():
            model = model.to("cuda")
            logger.info("Nougat model loaded on CUDA")
        else:
            logger.info("Nougat model loaded on CPU")

        _MODEL_CACHE = model
        _PROCESSOR_CACHE = processor
        return _MODEL_CACHE, _PROCESSOR_CACHE

    except Exception as e:
        raise RuntimeError(f"Failed to initialize Nougat model: {e}") from e


class NougatParser(BaseParser):
    """Parser for scientific PDF documents using Meta's Nougat model.

    Converts PDF pages to images and runs a vision transformer to produce
    Mathpix Markdown (.mmd) output with LaTeX math and table preservation.
    No image extraction — Nougat outputs text only.
    """

    def extract_text(self) -> str:
        """Extract text using Nougat."""
        text, _ = self.extract_text_and_images_dict({})
        return text

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract text from PDF using Nougat. No image extraction.

        Args:
            parent_data: Dictionary with parent document data (unused for image extraction)

        Returns:
            Tuple of (markdown_text, []) — Nougat does not extract images
        """
        model, processor = _get_model_and_processor()

        try:
            import fitz  # pymupdf
            import torch
            from PIL import Image
            import io
        except ImportError as e:
            raise ImportError(
                "pymupdf not installed. Run: source scripts/nersc_setup_nougat.sh"
            ) from e

        try:
            doc = fitz.open(str(self.file_path))
            images = []
            for page in doc:
                mat = fitz.Matrix(150 / 72, 150 / 72)  # 150 dpi
                pix = page.get_pixmap(matrix=mat)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                images.append(img)
            doc.close()
        except Exception as e:
            raise RuntimeError(f"Failed to rasterize PDF {self.file_path}: {e}") from e

        if not images:
            raise RuntimeError(f"No pages extracted from {self.file_path}")

        logger.info(f"Processing {len(images)} page(s) with Nougat: {self.file_path}")

        page_texts = []
        batch_size = 4

        try:
            for i in range(0, len(images), batch_size):
                batch = images[i : i + batch_size]
                inputs = processor(batch, return_tensors="pt").to(model.device, dtype=model.dtype)

                with torch.no_grad():
                    outputs = model.generate(
                        **inputs,
                        min_length=1,
                        max_new_tokens=3584,
                        bad_words_ids=[[processor.tokenizer.unk_token_id]],
                        return_dict_in_generate=True,
                    )

                generated = processor.batch_decode(
                    outputs.sequences, skip_special_tokens=True
                )
                generated = processor.post_process_generation(
                    generated, fix_markdown=False
                )

                if isinstance(generated, str):
                    generated = [generated]

                page_texts.extend(generated)

        except Exception as e:
            logger.error(f"Error during Nougat inference on {self.file_path}: {e}", exc_info=True)
            if page_texts:
                logger.warning("Returning partial results from pages processed before error")
            else:
                raise

        text = "\n\n".join(page_texts)
        return text, []
