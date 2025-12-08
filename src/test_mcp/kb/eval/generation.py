"""Question generation for evaluation datasets - database integration."""

import logging
import random
import socket
import time
from pathlib import Path
from typing import Dict, List, Optional

from .core import EvalGeneration, EvalDataset
from ..base import get
from ..database import get_db_session
from ...eval.generation import generate_qa_pairs_keypoint, generate_qa_pairs_persona

logger = logging.getLogger(__name__)


def generate_questions_from_documents(
    document_ids: List[str],
    num_questions_per_doc: int = 1,
    generation_method: str = "keypoint",
    personas: Optional[List[str]] = None,
    model: Optional[str] = None,
    session=None,
) -> Dict:
    """Generate evaluation questions from documents using LLM.

    Args:
        document_ids: List of document IDs to generate questions from
        num_questions_per_doc: Number of questions to generate per document
        generation_method: Method to use ('keypoint' or 'persona')
        personas: List of personas (used if method='persona')
        model: LLM model to use (defaults to EVAL_MODEL env var)
        session: Database session

    Returns:
        Dict with generation statistics:
        {
            "generation_id": str,
            "num_documents_processed": int,
            "num_questions_generated": int,
            "total_time_seconds": float,
            "method": str,
        }

    Generation methods:
        - 'keypoint': Extract key facts and generate questions (single LLM call)
        - 'persona': Generate questions from different user perspectives
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        all_questions = []
        total_time = 0.0
        generation_metadata = None

        for doc_id in document_ids:
            # Get document
            doc = get(uuid=doc_id, session=session)
            if not doc:
                logger.warning(f"Document not found: {doc_id}")
                continue

            if not doc.text or not doc.text.strip():
                logger.warning(f"Document {doc_id} has no text content")
                continue

            logger.info(f"Generating {num_questions_per_doc} questions from document {doc_id} using {generation_method} method")

            # Generate QA pairs based on method
            start_time = time.time()

            if generation_method == "keypoint":
                result = generate_qa_pairs_keypoint(
                    doc.text,
                    num_questions=num_questions_per_doc,
                    model=model,
                )
            elif generation_method == "persona":
                result = generate_qa_pairs_persona(
                    doc.text,
                    num_questions=num_questions_per_doc,
                    personas=personas,
                    model=model,
                )
            else:
                raise ValueError(f"Unknown generation method: {generation_method}")

            elapsed_time = time.time() - start_time
            total_time += elapsed_time

            # Extract QA pairs from result
            # Handle case where result might be a list (error case) or dict
            if isinstance(result, list):
                logger.warning(f"Generation function returned list instead of dict, treating as empty result")
                qa_pairs = []
            else:
                qa_pairs = result.get("qa_pairs", [])

            # Store generation metadata from first call (same for all documents)
            if generation_metadata is None:
                generation_metadata = {
                    "type": result.get("type"),
                    "model": result.get("model"),
                    "prompt": result.get("prompt"),
                    "generation_method": generation_method,
                    "personas": personas if generation_method == "persona" else None,
                }

            # Create question records
            for i, pair in enumerate(qa_pairs):
                # Calculate per-question time (approximate)
                question_time = elapsed_time / len(qa_pairs) if qa_pairs else 0.0

                question_meta = {"index": i}
                if generation_method == "persona":
                    question_meta["persona"] = pair.get("persona")

                question = EvalDataset(
                    question=pair["question"],
                    source_document_id=doc.id,
                    answer=pair.get("answer"),  # Used in persona mode
                    keypoints=pair.get("keypoint"),  # Used in keypoint mode
                    generation_time_seconds=question_time,
                    hostname=socket.gethostname(),
                    meta=question_meta,
                )
                all_questions.append(question)

            logger.info(f"Generated {len(qa_pairs)} questions for document {doc_id} in {elapsed_time:.2f}s")

        # Create generation record if questions were generated
        if all_questions:
            # Determine source_id if all documents are from same source
            source_ids = set()
            for q in all_questions:
                if q.source_document_id:
                    doc = get(uuid=q.source_document_id, session=session)
                    if doc:
                        source_ids.add(doc.source_id)

            source_id = source_ids.pop() if len(source_ids) == 1 else None

            # Move model to meta, keep generation_method as column
            meta_without_method = {k: v for k, v in generation_metadata.items() if k != "generation_method"}
            
            generation = EvalGeneration(
                generation_type="synthetic",
                generation_method=generation_method,
                prompt=generation_metadata.get("prompt"),
                source_id=source_id,
                source_type="text",
                meta=meta_without_method,  # Includes model, type, prompt, personas
            )
            session.add(generation)
            session.flush()  # Get generation ID

            # Link questions to generation
            for question in all_questions:
                question.generation_id = generation.id
                session.add(question)

            session.commit()

            logger.info(
                f"Generated {len(all_questions)} questions from {len(document_ids)} documents "
                f"in {total_time:.2f}s using {generation_method} method"
            )

            return {
                "generation_id": generation.id,
                "num_documents_processed": len(document_ids),
                "num_questions_generated": len(all_questions),
                "total_time_seconds": total_time,
                "method": generation_method,
            }
        else:
            logger.warning("No questions were generated")
            return {
                "generation_id": None,
                "num_documents_processed": 0,
                "num_questions_generated": 0,
                "total_time_seconds": 0.0,
                "method": generation_method,
            }

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error generating questions: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


def generate_questions_from_source(
    source_id: str,
    doc_type: str = "text",
    num_documents: Optional[int] = None,
    num_questions_per_doc: int = 5,
    generation_method: str = "keypoint",
    session=None,
    **kwargs
) -> Dict:
    """Generate questions from documents in a source.

    Randomly samples documents from the source and generates questions.

    Args:
        source_id: Source ID to generate from
        doc_type: Filter by document type (default: "text")
        num_documents: Number of documents to sample (default: all)
        num_questions_per_doc: Number of questions per document
        generation_method: Method to use ('keypoint' or 'persona')
        session: Database session
        **kwargs: Additional arguments passed to generate_questions_from_documents

    Returns:
        Dict with generation statistics (same as generate_questions_from_documents)
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get documents from source
        docs = get(
            source_id=source_id,
            doc_type=doc_type,
            session=session,
        )

        if not docs:
            raise ValueError(f"No documents found for source {source_id} with doc_type={doc_type}")

        # Convert to list if single document
        if not isinstance(docs, list):
            docs = [docs]

        # Randomly sample if num_documents specified
        if num_documents is not None and num_documents < len(docs):
            docs = random.sample(docs, num_documents)
            logger.info(f"Randomly sampled {num_documents} documents from {len(docs)} total")

        document_ids = [doc.id for doc in docs]

        logger.info(f"Generating questions from {len(document_ids)} documents in source {source_id}")

        # Pass session to generate_questions_from_documents
        result = generate_questions_from_documents(
            document_ids=document_ids,
            num_questions_per_doc=num_questions_per_doc,
            generation_method=generation_method,
            session=session,
            **kwargs
        )

        return result

    finally:
        if own_session:
            session.__exit__(None, None, None)


def import_questions_from_file(
    file_path: Path,
    format: str = "json",
    generation_type: str = "human",
    session=None,
) -> Dict:
    """Import pre-existing questions from a file.

    Not yet implemented - placeholder for future functionality.

    Args:
        file_path: Path to questions file
        format: File format ('json', 'csv', 'jsonl')
        generation_type: Type of generation (default: 'human')
        session: Database session

    Returns:
        Dict with import statistics

    Expected JSON format:
    [
        {
            "question": "What is the flux?",
            "source_document_id": "abc-123",  # Optional
            "answer": "42",  # Optional
            "keypoints": "The flux is 42",  # Optional
            "meta": {}  # Optional
        },
        ...
    ]
    """
    _ = file_path, format, generation_type, session  # Suppress unused warnings
    raise NotImplementedError("import_questions_from_file not yet implemented")
