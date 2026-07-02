"""Loader for the Mu2e hand-curated benchmark question set."""

import json
import logging
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

from .db_models import EvalGeneration, EvalDataset
from ..database import get_db_session

logger = logging.getLogger(__name__)

BENCHMARK_GENERATION_NAME = "mu2e-benchmark"
BENCHMARK_GENERATION_TYPE = "human"
BENCHMARK_GENERATION_METHOD = "mu2e_benchmark"


def load_mu2e_benchmark(
    json_path: str | Path,
    session=None,
) -> Dict[str, Any]:
    """Load hand-written Mu2e benchmark questions into the eval system.

    Creates an EvalGeneration record (type='human', method='mu2e_benchmark')
    and EvalDataset entries for each question. Uses content hashing for
    deduplication — safe to call multiple times.

    Args:
        json_path: Path to the mu2e_benchmark.json file.
        session: Optional database session.

    Returns:
        Dict with generation_id, num_questions_loaded, num_skipped.
    """
    json_path = Path(json_path)
    if not json_path.exists():
        raise FileNotFoundError(f"Benchmark file not found: {json_path}")

    with open(json_path) as f:
        data = json.load(f)

    questions = data.get("questions", [])
    if not questions:
        raise ValueError("No questions found in benchmark file")

    benchmark_name = data.get("name", BENCHMARK_GENERATION_NAME)
    benchmark_version = data.get("version", "1.0")

    with get_db_session(session) as session:
        # Find or create the generation record
        generation = (
            session.query(EvalGeneration)
            .filter(
                EvalGeneration.generation_type == BENCHMARK_GENERATION_TYPE,
                EvalGeneration.generation_method == BENCHMARK_GENERATION_METHOD,
            )
            .first()
        )

        if generation is None:
            generation = EvalGeneration(
                id=str(uuid.uuid4()),
                name=benchmark_name,
                generation_type=BENCHMARK_GENERATION_TYPE,
                generation_method=BENCHMARK_GENERATION_METHOD,
                source_id="mu2e-wiki",
                source_type="text",
                meta={
                    "version": benchmark_version,
                    "description": data.get("description", ""),
                    "file": str(json_path.name),
                },
            )
            session.add(generation)
            session.flush()
            logger.info(f"Created benchmark generation: {generation.id}")
        else:
            logger.info(f"Found existing benchmark generation: {generation.id}")

        # Load questions, skip duplicates by checking question text
        existing_questions = {
            q.question
            for q in session.query(EvalDataset.question)
            .filter(EvalDataset.generation_id == generation.id)
            .all()
        }

        num_loaded = 0
        num_skipped = 0

        for q in questions:
            question_text = q["question"]

            if question_text in existing_questions:
                num_skipped += 1
                continue

            entry = EvalDataset(
                id=str(uuid.uuid4()),
                question=question_text,
                generation_id=generation.id,
                source_document_id=q.get("source_document_id"),
                answer=q.get("answer"),
                meta={
                    "bucket": q.get("bucket", "unknown"),
                    "expected_source": q.get("expected_source", ""),
                },
            )
            session.add(entry)
            num_loaded += 1

        session.commit()

        logger.info(
            f"Benchmark loaded: {num_loaded} new, {num_skipped} skipped "
            f"(generation {generation.id})"
        )

        return {
            "generation_id": generation.id,
            "generation_name": benchmark_name,
            "num_questions_loaded": num_loaded,
            "num_skipped": num_skipped,
            "total_questions": num_loaded + num_skipped,
        }
