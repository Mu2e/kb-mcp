"""Azure AI Document Intelligence parser for high-quality PDF parsing with figure extraction."""

import logging
import os
from pathlib import Path
from typing import List, Tuple, Any

from dotenv import load_dotenv

from .parser_base import BaseParser

load_dotenv()

logger = logging.getLogger(__name__)


def _get_client() -> Any:
    """Create and return an Azure Document Intelligence client."""
    try:
        from azure.core.credentials import AzureKeyCredential
        from azure.ai.documentintelligence import DocumentIntelligenceClient
    except ImportError:
        logger.error(
            "azure-ai-documentintelligence not installed. "
            "Install with: pip install azure-ai-documentintelligence"
        )
        return None

    endpoint = os.environ.get("AZURE_DI_ENDPOINT")
    key = os.environ.get("AZURE_DI_KEY")

    if not endpoint or not key:
        logger.error(
            "AZURE_DI_ENDPOINT and AZURE_DI_KEY environment variables must be set."
        )
        return None

    return DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key))


class AzureParser(BaseParser):
    """Parser for PDF documents using Azure AI Document Intelligence."""

    def extract_text(self) -> str:
        text, _ = self.extract_text_and_images_dict({})
        return text

    def extract_text_and_images_dict(
        self,
        parent_data: dict,
    ) -> Tuple[str, List[dict]]:
        """Extract text and figures from PDF using Azure Document Intelligence.

        Uses the prebuilt-layout model with figure output enabled.
        Figures are retrieved as pre-cropped images from the service.

        Args:
            parent_data: Dictionary with parent document data

        Returns:
            Tuple of (markdown_text, list_of_image_dicts)
        """
        try:
            from azure.ai.documentintelligence.models import (
                AnalyzeOutputOption,
                DocumentContentFormat,
            )
        except ImportError:
            logger.error(
                "azure-ai-documentintelligence not installed. "
                "Install with: pip install azure-ai-documentintelligence"
            )
            return "", []

        client = _get_client()
        if client is None:
            return "", []

        try:
            with open(self.file_path, "rb") as f:
                file_bytes = f.read()

            logger.info(f"Submitting {self.file_path.name} to Azure Document Intelligence...")

            poller = client.begin_analyze_document(
                "prebuilt-layout",
                file_bytes,
                output_content_format=DocumentContentFormat.MARKDOWN,
                output=[AnalyzeOutputOption.FIGURES],
            )
            result = poller.result()
            operation_id = poller.details["operation_id"]

            text = result.content or ""

            image_dicts = []
            figures = getattr(result, "figures", None) or []

            for image_number, figure in enumerate(figures, start=1):
                figure_id = figure.id  # e.g. "1.1" (page.index)

                # Extract caption text if present
                caption_text = None
                if figure.caption:
                    caption_text = figure.caption.content

                # Determine page number from bounding regions
                page = None
                if figure.bounding_regions:
                    page = figure.bounding_regions[0].page_number

                # Retrieve cropped figure image bytes from the service
                try:
                    img_data_iter = client.get_analyze_result_figure(
                        model_id="prebuilt-layout",
                        result_id=operation_id,
                        figure_id=figure_id,
                    )
                    img_bytes = b"".join(img_data_iter)
                except Exception as e:
                    logger.warning(f"Could not retrieve figure {figure_id}: {e}")
                    continue

                if not img_bytes:
                    continue

                image_name = f"figure_{figure_id}.png"
                doc_id = parent_data.get("doc_id", self.file_path.stem) + "-" + image_name

                img_dict = {
                    "source_id": parent_data.get("source_id", "local"),
                    "doc_id": doc_id,
                    "doc_type": "image",
                    "source_type": parent_data.get("source_type"),
                    "binary": img_bytes,
                    "parent_id": parent_data.get("id"),
                    "meta": {
                        "image_name": image_name,
                        "image_number": image_number,
                        "parent_doc_id": parent_data.get("id", parent_data.get("doc_id")),
                        "parser": "azure",
                    },
                }

                if page is not None:
                    img_dict["meta"]["page"] = page

                if caption_text:
                    img_dict["meta"]["caption"] = caption_text

                if "meta" in parent_data:
                    img_dict["meta"].update(
                        {k: v for k, v in parent_data["meta"].items() if k not in img_dict["meta"]}
                    )

                image_dicts.append(img_dict)

            logger.info(
                f"Azure DI: extracted {len(text)} chars, {len(image_dicts)} figures "
                f"from {self.file_path.name}"
            )
            return text, image_dicts

        except Exception as e:
            logger.error(f"Error parsing with Azure Document Intelligence: {e}", exc_info=True)
            return "", []
