"""LLM-based knowledge graph extraction from documents."""

import json
import logging
import socket
import time
from typing import List, Dict, Any, Optional

from tqdm import tqdm

from ..database import get_db_session
from ..db_models import Document
from ...llm import get_openai_client
from ...llm.usage import STAGE_GRAPH_EXTRACTION, record_llm_usage
from ...config import get_graph_config
from .graph import add_relation, get_node_types, get_verbs
from .db_models import GraphExtractionLog

logger = logging.getLogger(__name__)


def extract_relations(
    text: str,
    title: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    domain_context: Optional[str] = None,
    session=None,
    document_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Extract knowledge graph relations from a document using LLM.

    Args:
        text: Text content to extract relations from.
        title: Document title (for context).
        metadata: Additional metadata (not used currently).
        session: Optional database session.
        document_id: Document the text came from. Only used to attribute
            token usage; extraction itself doesn't need it.

    Returns:
        List of extracted relation dictionaries with schema:
        {
            "source_name": str,
            "source_type": str,
            "verb": str,
            "target_name": str,
            "target_type": str,
            "justification": str,
            "confidence": float
        }

    Raises:
        ValueError: If document not found.
    """
    with get_db_session(session) as session:
        # Get node types and verbs for prompt
        node_types = get_node_types(session)
        verbs = get_verbs(session)

        graph_config = get_graph_config()
        model = graph_config['graph_relation_extraction_model']

        types_str = "\n".join([f"- {k}: {v}" for k, v in node_types.items()])
        verbs_str = "\n".join([f"- {k}: {v}" for k, v in verbs.items()])

        context_str = ""
        if title:
            context_str = f"""### CONTEXT
        Text from document: **"{title}"**"""
        if metadata:
            if context_str == "":
                context_str = "### CONTEXT"
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    context_str += f"\n- {k}: {v}"
                if isinstance(v, list):
                    context_str += f"\n- {k}: {', '.join(map(str, v))}"

        domain_str = ""
        if domain_context:
            domain_str = f"""### DOMAIN CONTEXT
        {domain_context}
        """

        prompt = f"""Extract knowledge graph relations from the following document fragment.

        {context_str}

        {domain_str}

        ### DEFINITIONS
        You must ONLY use the keys from the lists below.

        **Node Types (Use ONLY the Label):**
        {types_str}

        **Verbs (Use ONLY the Name):**
        {verbs_str}

        ### EXTRACTION RULES
        - **Canonical Names:** Use concise, standalone names (e.g., "VXD3", not "the new detector").
        - **Explicit Only:** Do not guess. If it's not written, don't extract it.
        - **No Orphans:** Ignore pronouns if you cannot resolve them to a specific named entity.
        - **Quantities vs. Concepts:** When extracting measurements, use the Metric/Concept as the target_name (e.g., "Energy Resolution", not "Energy Resolution of 5%"). The specific numerical value MUST go in the justification.

        ### OUTPUT FORMAT
        Return valid raw JSON. Do NOT use markdown code blocks.
        {{
        "relations": [
            {{
            "source_name": "Entity Name",
            "source_type": "Label from Node Types",
            "verb": "Name from Verbs",
            "target_name": "Entity Name",
            "target_type": "Label from Node Types",
            "justification": "Exact quote from text",
            "confidence": 0.95
            }}
        ]
        }}

        [[TEXT TO ANALYZE]]
        {text}
        """

        # Call LLM
        try:
            client = get_openai_client(model=model)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a knowledge graph extraction assistant. Extract structured relations from documents. Always return valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                response_format={"type": "json_object"},
                max_tokens=4096 
            )

            record_llm_usage(
                getattr(response, "usage", None),
                stage=STAGE_GRAPH_EXTRACTION,
                model=model,
                document_id=document_id,
            )

            # Parse response
            content = response.choices[0].message.content.strip()

            # Handle markdown code blocks
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            # Parse JSON
            result = json.loads(content)

            # Validate structure
            if "relations" not in result:
                logger.error(f"LLM response missing 'relations' key: {content[:200]}")
                return []

            relations = result["relations"]
            if not isinstance(relations, list):
                logger.error(f"'relations' is not a list: {type(relations)}")
                return []

            # Validate each relation
            valid_relations = []
            required_fields = ["source_name", "source_type", "verb", "target_name", "target_type"]

            for i, relation in enumerate(relations):
                # Check required fields
                missing_fields = [field for field in required_fields if field not in relation]
                if missing_fields:
                    logger.warning(f"Relation {i} missing fields {missing_fields}, skipping")
                    continue

                # Validate types and verbs
                if relation["source_type"] not in node_types:
                    logger.warning(f"Relation {i} has invalid source_type '{relation['source_type']}', skipping")
                    continue
                if relation["target_type"] not in node_types:
                    logger.warning(f"Relation {i} has invalid target_type '{relation['target_type']}', skipping")
                    continue
                if relation["verb"] not in verbs:
                    logger.warning(f"Relation {i} has invalid verb '{relation['verb']}', skipping")
                    continue

                # Add defaults for optional fields
                if "justification" not in relation:
                    relation["justification"] = None
                if "confidence" not in relation:
                    relation["confidence"] = 0.8  # Default confidence

                valid_relations.append(relation)

            logger.info(f"Extracted {len(valid_relations)} valid relations (LLM returned {len(relations)} total)")
            return valid_relations

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"Response content: {content[:500]}")
            return []
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}", exc_info=True)
            return []


def process_relations(
    extracted_relations: List[Dict[str, Any]],
    document_id: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """
    Process extracted relations, adding them to graph database one by one.

    Args:
        extracted_relations: List of relation dicts from extract_relations_from_document().
        document_id: Document ID for evidence tracking.
        session: Database session (optional).

    Returns:
        Statistics dict:
        {
            "total": int,
            "created": int,
            "updated": int,
            "errors": int,
            "error_details": List[Dict[str, Any]]
        }
    """
    total = len(extracted_relations)
    created = 0
    updated = 0
    errors = 0
    error_details = []

    logger.info(f"Processing {total} extracted relations for document {document_id}")

    graph_config = get_graph_config()
    extraction_model = graph_config['graph_relation_extraction_model']

    with get_db_session(session) as session:
        for i, relation in enumerate(tqdm(extracted_relations, desc="Processing relations", unit="relation")):
            try:
                # Call add_relation
                graph_relation, is_new = add_relation(
                    source_type=relation['source_type'],
                    source_name=relation['source_name'],
                    verb=relation['verb'],
                    target_type=relation['target_type'],
                    target_name=relation['target_name'],
                    evidence_document_id=document_id,
                    evidence_text=relation.get('justification'),
                    confidence=relation.get('confidence'),
                    extraction_model=extraction_model,
                    session=session
                )

                # Track created vs updated
                if is_new:
                    created += 1
                else:
                    updated += 1

                # Commit after each relation (per-item commit for safety)
                session.commit()

                logger.debug(f"Processed relation {i+1}/{total}: {relation['source_name']} --[{relation['verb']}]--> {relation['target_name']}")

            except ValueError as e:
                # Validation error (e.g., invalid node type)
                errors += 1
                error_msg = str(e)
                error_details.append({"relation": relation, "error": error_msg})
                logger.warning(f"Relation {i+1}/{total} failed validation: {error_msg}")
                session.rollback()
                continue

            except KeyError as e:
                # Missing field (shouldn't happen if validation passed)
                errors += 1
                error_msg = f"Missing field: {e}"
                error_details.append({"relation": relation, "error": error_msg})
                logger.warning(f"Relation {i+1}/{total} missing field: {e}")
                session.rollback()
                continue

            except Exception as e:
                # Other errors
                errors += 1
                error_msg = str(e)
                error_details.append({"relation": relation, "error": error_msg})
                logger.error(f"Relation {i+1}/{total} failed: {e}", exc_info=True)
                session.rollback()
                continue

    # Log summary
    logger.info(f"Processed {total} relations: {created} created, {updated} updated, {errors} errors")

    return {
        "total": total,
        "created": created,
        "updated": updated,
        "errors": errors,
        "error_details": error_details
    }


def extract_and_process_document(
    document_id: str,
    domain_context: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """
    Extract and process relations from a document with logging.

    This is the high-level function that:
    1. Fetches document from database
    2. Extracts relations using LLM
    3. Processes relations into graph database
    4. Logs the run with timing and statistics

    Args:
        document_id: Document ID to process.
        session: Optional database session.

    Returns:
        Dict with extraction results and statistics:
        {
            "log_id": str,  # ID of the extraction log entry
            "document_id": str,
            "relations_extracted": int,
            "relations_processed": int,
            "relations_created": int,
            "relations_updated": int,
            "relations_errors": int,
            "time_extraction": float,
            "time_processing": float,
            "extraction_model": str,
            "hostname": str,
            "error_details": List[dict]
        }

    Raises:
        ValueError: If document not found.
    """
    from ..documents import get as get_document

    # Get hostname
    hostname = socket.gethostname()

    # Get extraction model from config
    graph_config = get_graph_config()
    extraction_model = graph_config['graph_relation_extraction_model']

    with get_db_session(session) as session:
        # Fetch document
        document = get_document(document_id, session=session)
        if not document:
            raise ValueError(f"Document {document_id} not found")

        logger.info(f"Starting extraction for document {document_id} on {hostname}")

        # Resolve domain context from config if not explicitly provided
        if domain_context is None:
            domain = graph_config.get('domain')
            if domain == 'mu2e':
                from .mu2e_extraction import MU2E_DOMAIN_CONTEXT
                domain_context = MU2E_DOMAIN_CONTEXT
                logger.info("Using Mu2e domain context for extraction")

        # Step 1: Extract relations from document text
        start_extraction = time.time()
        try:
            extracted_relations = extract_relations(
                text=document.text,
                title=document.title or document.title_gen,
                metadata={
                    "source_id": document.source_id,
                    "doc_id": document.doc_id,
                },
                domain_context=domain_context,
                session=session,
                document_id=document_id,
            )
            time_extraction = time.time() - start_extraction
            logger.info(f"Extraction completed in {time_extraction:.2f}s, found {len(extracted_relations)} relations")
        except Exception as e:
            logger.error(f"Extraction failed for document {document_id}: {e}", exc_info=True)
            # Log failed extraction
            log_entry = GraphExtractionLog(
                document_id=document_id,
                hostname=hostname,
                extraction_model=extraction_model,
                time_extraction=None,
                time_processing=None,
                relations_extracted=0,
                relations_processed=0,
                relations_created=0,
                relations_updated=0,
                relations_errors=1,
                error_details=[{"error": str(e), "stage": "extraction"}],
                meta={"exception": str(type(e).__name__)}
            )
            session.add(log_entry)
            session.commit()
            raise

        # Step 2: Process relations into database
        start_processing = time.time()
        processing_stats = process_relations(
            extracted_relations=extracted_relations,
            document_id=document_id,
            session=session
        )
        time_processing = time.time() - start_processing
        logger.info(f"Processing completed in {time_processing:.2f}s")

        # Step 3: Create extraction log entry
        log_entry = GraphExtractionLog(
            document_id=document_id,
            hostname=hostname,
            extraction_model=extraction_model,
            time_extraction=time_extraction,
            time_processing=time_processing,
            relations_extracted=len(extracted_relations),
            relations_processed=processing_stats["total"],
            relations_created=processing_stats["created"],
            relations_updated=processing_stats["updated"],
            relations_errors=processing_stats["errors"],
            error_details=processing_stats.get("error_details"),
            meta={
                "document_title": document.title or document.title_gen,
                "source_id": document.source_id,
                "doc_id": document.doc_id,
            }
        )
        session.add(log_entry)
        session.commit()

        logger.info(
            f"Extraction complete for document {document_id}: "
            f"{len(extracted_relations)} extracted, "
            f"{processing_stats['updated']} processed, "
            f"{processing_stats['errors']} errors"
        )

        return {
            "log_id": log_entry.id,
            "document_id": document_id,
            "relations_extracted": len(extracted_relations),
            "relations_processed": processing_stats["total"],
            "relations_created": processing_stats["created"],
            "relations_updated": processing_stats["updated"],
            "relations_errors": processing_stats["errors"],
            "time_extraction": time_extraction,
            "time_processing": time_processing,
            "extraction_model": extraction_model,
            "hostname": hostname,
            "error_details": processing_stats.get("error_details", [])
        }
