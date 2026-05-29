"""Parser comparison: compare extracted text from multiple parsers for the same raw document."""

import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db_session
from .db_models import Document, ParserCategories, ParserComparison, RawDocument

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 24000  # ~6k tokens per document; keep total prompt manageable


def _get_openai_client(model: Optional[str] = None):
    from ..llm import get_openai_client
    return get_openai_client(model)


def _default_model(for_categorize: bool = False) -> str:
    from ..config import get_llm_config
    cfg = get_llm_config()
    return cfg["eval_judge_model"] if for_categorize else cfg["parser_comparison_model"]


def _fix_json_escapes(s: str) -> str:
    return re.sub(r'\\(?![\\"/bfnrtu0-9])', r'\\\\', s)


def _call_llm(client, model: str, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Pass 1: compare parsers for a single raw document
# ---------------------------------------------------------------------------

def _build_comparison_prompt(docs: List[Document]) -> str:
    sections = []
    for doc in docs:
        text = (doc.text or "").strip()
        if len(text) > _MAX_TEXT_CHARS:
            text = text[:_MAX_TEXT_CHARS] + "\n[... truncated ...]"
        parser = doc.parser_id or "unknown"
        sections.append(f"=== Parser: {parser} ===\n{text}")

    joined = "\n\n".join(sections)
    return (
        "The following sections each contain text extracted from the same raw document "
        "by a different parser.\n\n"
        "Return a JSON object with exactly three fields:\n\n"
        '1. "document_description": 1-3 sentences describing the document itself '
        "(type, subject, structural features like dense equations, tables, scanned text, "
        "figures, handwriting, multi-column layout). Parser-agnostic.\n\n"
        '2. "features_present": A JSON object indicating which content types are present '
        "in this document, with boolean values. Keys: "
        '"equations", "tables", "figures", "handwriting", "multi_column", '
        '"footnotes_or_marginalia", "degraded_scan".\n\n'
        '3. "per_parser_observations": A JSON object with one key per parser present. '
        "Each value is itself an object with these keys, each containing 1-2 sentences:\n"
        '   - "equations": how this parser handled equations (or "N/A" if no equations)\n'
        '   - "tables": how this parser handled tables (or "N/A")\n'
        '   - "figures": how this parser handled figures (or "N/A")\n'
        '   - "ocr_quality": observations on character-level accuracy\n'
        '   - "structure": observations on heading/list/reading-order preservation\n'
        '   - "failures": any catastrophic failures observed (repetition loops, truncation, '
        "garbled output, missing sections, hallucinated content). Empty string if none.\n"
        '   - "notable_strength": single most notable thing this parser did well. Empty string if nothing stood out.\n\n'
        "You are comparing parser outputs to each other, not to ground truth. "
        "Do not assert which output is correct unless you can point to a specific reason "
        '(e.g. internal inconsistency, obvious OCR error like "1010" where context implies "10^10"). '
        "Where parsers disagree and you cannot tell which is right, say so explicitly.\n\n"
        "Return ONLY valid JSON.\n\n"
        f"{joined}"
    )


def compare_raw_document(
    raw_document_id: str,
    parsers: Optional[List[str]] = None,
    model: Optional[str] = None,
    force: bool = False,
    session=None,
) -> Dict[str, Any]:
    """Run pass-1 comparison for one raw document.

    Fetches all Document rows linked to `raw_document_id` (optionally filtered by
    `parsers`), sends their texts to an LLM, and stores the result in
    `parser_comparisons`.

    Args:
        raw_document_id: UUID of the RawDocument to compare.
        parsers: Optional list of parser names to restrict comparison to.
        model: LLM model override.
        force: Re-run even if a comparison already exists.
        session: Optional existing DB session.

    Returns:
        Dict with keys: raw_document_id, comparison_id, parsers_compared, skipped, error.
    """
    if model is None:
        model = _default_model()

    with get_db_session(session) as db:
        raw_doc = db.query(RawDocument).filter(RawDocument.id == raw_document_id).first()
        if not raw_doc:
            return {"raw_document_id": raw_document_id, "error": "Raw document not found", "skipped": True}

        query = db.query(Document).filter(
            Document.raw_document_id == raw_document_id,
            Document.doc_type != "image",
            Document.text.isnot(None),
        )
        if parsers:
            query = query.filter(Document.parser_id.in_(parsers))
        docs = query.order_by(Document.parser_id).all()

        if len(docs) < 2:
            return {
                "raw_document_id": raw_document_id,
                "skipped": True,
                "reason": f"Only {len(docs)} parsed document(s) found — need at least 2 to compare.",
                "parsers_found": [d.parser_id for d in docs],
            }

        parser_ids = [d.parser_id for d in docs]
        document_ids = [d.id for d in docs]

        # Check for existing comparison unless force
        if not force:
            existing = (
                db.query(ParserComparison)
                .filter(ParserComparison.raw_document_id == raw_document_id)
                .first()
            )
            if existing:
                return {
                    "raw_document_id": raw_document_id,
                    "comparison_id": existing.id,
                    "skipped": True,
                    "reason": "Comparison already exists. Use --force to re-run.",
                    "parsers_compared": existing.parser_ids,
                }

        prompt = _build_comparison_prompt(docs)
        system = (
            "You are an expert at evaluating document parsing quality. "
            "Provide clear, specific analysis that helps users choose the best parser. "
            "Always respond with valid JSON."
        )

        t0 = time.time()
        try:
            client = _get_openai_client(model)
            raw_response = _call_llm(client, model, system, prompt)
        except Exception as e:
            logger.error(f"LLM call failed for raw_document_id={raw_document_id}: {e}")
            return {"raw_document_id": raw_document_id, "error": str(e), "skipped": False}
        elapsed = time.time() - t0

        # Parse JSON response; fall back to storing raw response as comparison text
        comparison_text = raw_response
        document_description = None
        features_present = None
        per_parser_observations = None
        try:
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
            parsed = json.loads(json_match.group(1) if json_match else raw_response)
        except (json.JSONDecodeError, AttributeError):
            try:
                parsed = json.loads(_fix_json_escapes(raw_response))
            except (json.JSONDecodeError, AttributeError):
                parsed = None
                logger.warning(f"Could not parse JSON response for {raw_document_id}, storing as plain text")

        if parsed:
            document_description = parsed.get("document_description")
            features_present = parsed.get("features_present")
            per_parser_observations = parsed.get("per_parser_observations")
            # Build readable comparison text from structured per-parser observations
            if per_parser_observations:
                lines = []
                for parser_name, obs in per_parser_observations.items():
                    lines.append(f"### {parser_name}")
                    for key, val in obs.items():
                        if val and val != "N/A":
                            lines.append(f"**{key}**: {val}")
                    lines.append("")
                comparison_text = "\n".join(lines)
            else:
                comparison_text = parsed.get("comparison", raw_response)

        record = ParserComparison(
            id=str(uuid.uuid4()),
            raw_document_id=raw_document_id,
            document_ids=document_ids,
            parser_ids=parser_ids,
            document_description=document_description,
            comparison=comparison_text,
            categories={"features_present": features_present, "per_parser_observations": per_parser_observations} if (features_present or per_parser_observations) else None,
            model=model,
            meta={"elapsed_seconds": round(elapsed, 2), "prompt_chars": len(prompt)},
        )
        db.add(record)
        db.flush()
        comparison_id = record.id

    logger.info(f"Stored comparison {comparison_id} for raw_document_id={raw_document_id} ({elapsed:.1f}s)")
    return {
        "raw_document_id": raw_document_id,
        "comparison_id": comparison_id,
        "parsers_compared": parser_ids,
        "skipped": False,
        "elapsed_seconds": round(elapsed, 2),
    }


def compare_source(
    source_id: str,
    parsers: Optional[List[str]] = None,
    model: Optional[str] = None,
    force: bool = False,
    limit: Optional[int] = None,
    workers: int = 1,
    session=None,
) -> Dict[str, Any]:
    """Run pass-1 comparisons for all raw documents in a source.

    Args:
        source_id: Source ID (e.g. 'mu2e-docdb').
        parsers: Optional list of parser names to restrict comparison to.
        model: LLM model override.
        force: Re-run even if comparisons already exist.
        limit: Stop after this many raw documents.
        workers: Number of parallel workers (default 1).
        session: Optional existing DB session.

    Returns:
        Dict with aggregate counts.
    """
    with get_db_session(session) as db:
        query = db.query(RawDocument).filter(RawDocument.source_id == source_id)
        if limit:
            query = query.limit(limit)
        raw_doc_ids = [r.id for r in query.all()]

        # Pre-filter already-compared IDs to avoid race conditions with parallel workers
        if not force:
            already_done = {
                row.raw_document_id
                for row in db.query(ParserComparison.raw_document_id)
                .filter(ParserComparison.raw_document_id.in_(raw_doc_ids))
                .all()
            }
            pending_ids = [rid for rid in raw_doc_ids if rid not in already_done]
        else:
            already_done = set()
            pending_ids = raw_doc_ids

    total = len(raw_doc_ids)
    skipped = len(already_done)
    compared = errors = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    def _run(raw_id):
        return compare_raw_document(raw_id, parsers=parsers, model=model, force=force)

    with tqdm(total=len(pending_ids), desc="Comparing parsers", unit="doc") as pbar:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_run, rid): rid for rid in pending_ids}
            for future in as_completed(futures):
                result = future.result()
                if result.get("error"):
                    errors += 1
                    logger.warning(f"Error comparing {futures[future]}: {result['error']}")
                elif result.get("skipped"):
                    skipped += 1
                else:
                    compared += 1
                pbar.update(1)

    return {
        "source_id": source_id,
        "total_raw_documents": total,
        "compared": compared,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Pass 2: derive categories across all stored comparisons
# ---------------------------------------------------------------------------

def _build_categorize_prompt(comparisons: List[Dict[str, Any]], prompt_extra: Optional[str] = None) -> Tuple[str, str]:
    """Build the categorization prompt.

    Returns:
        (full_prompt, template) where template is the prompt without the document sections.
    """
    sections = []
    for i, cmp in enumerate(comparisons, 1):
        parsers = ", ".join(cmp.get("parser_ids") or [])
        desc = (cmp.get("document_description") or "").strip()
        features = cmp.get("features_present")
        per_parser = cmp.get("per_parser_observations")

        section = f"--- Document {i} (parsers: {parsers}) ---"
        if desc:
            section += f"\ndocument_description: {desc}"
        if features:
            section += f"\nfeatures_present: {json.dumps(features)}"
        if per_parser:
            section += f"\nper_parser_observations: {json.dumps(per_parser)}"
        elif cmp.get("comparison"):
            # Fallback for older rows that only have free-text comparison
            text = cmp["comparison"].strip()
            if len(text) > 2000:
                text = text[:2000] + "\n[... truncated ...]"
            section += f"\ncomparison: {text}"
        sections.append(section)

    joined = "\n\n".join(sections)
    template = (
        "Below are structured per-document parser analyses from a corpus of technical documents. "
        "Each entry contains a document description, a features_present object (boolean flags), "
        "and per_parser_observations with structured observations per parser per dimension.\n\n"
        "Aggregate these observations into a quantitative comparison. "
        "For each dimension (equations, tables, figures, ocr_quality, structure, failures), produce a summary that includes:\n\n"
        "- Number of documents in the corpus where this dimension was applicable "
        "(e.g. for 'equations', count documents where features_present.equations is true).\n"
        "- For each parser, count the number of those documents where the per-document observation indicated:\n"
        "    - clear success (parser handled it well)\n"
        "    - partial success (some content captured, some lost)\n"
        "    - clear failure (parser missed it, garbled it, or hallucinated)\n"
        "  Report as counts and fractions.\n"
        "- Most common failure modes per parser, with the number of documents on which each mode was observed.\n"
        "- A representative example per parser per dimension (1-2 sentences quoting the per-document observation).\n\n"
        "Also produce:\n"
        "- A failure-mode frequency table: rows are failure types (repetition loop, truncation, OCR garble, "
        "missing section, hallucinated content, formula placeholder, table collapse, etc.), "
        "columns are parsers, cells are counts.\n"
        "- A short note on document-type correlations: for each parser, which features_present flags "
        "correlate with that parser's failures.\n\n"
        "Do not produce a 'choose one parser' recommendation. Frame findings as tradeoffs and "
        "per-document-type failure modes.\n\n"
        "Be specific. Use counts, not adverbs like 'frequently' or 'rarely.'"
    )
    if prompt_extra:
        template += f"\n\nAdditional focus: {prompt_extra.strip()}"
    return template + f"\n\n{joined}", template


def _generate_title(client, model: str, categories_text: str, prompt_extra: Optional[str] = None) -> str:
    """Generate a short title for a categorization run from its output."""
    focus = f" The analysis focused on: {prompt_extra}." if prompt_extra else ""
    user = (
        f"Summarize the following parser comparison analysis in a concise title of 6-10 words.{focus} "
        f"Return only the title, no punctuation at the end.\n\n{categories_text[:3000]}"
    )
    try:
        return _call_llm(client, model, "You generate concise titles.", user).strip().strip(".")
    except Exception as e:
        logger.warning(f"Title generation failed: {e}")
        return ""


def categorize_comparisons(
    source_id: str,
    model: Optional[str] = None,
    prompt_extra: Optional[str] = None,
    title: Optional[str] = None,
    session=None,
) -> Dict[str, Any]:
    """Run pass-2 categorization over all stored comparisons for a source.

    Each call creates a new ParserCategories row — previous runs are preserved.

    Args:
        source_id: Source ID to categorize comparisons for.
        model: LLM model override.
        prompt_extra: Optional extra instructions appended to the base prompt.
        session: Optional existing DB session.

    Returns:
        Dict with keys: categories_id, comparisons_read, categories_text, model, elapsed_seconds.
    """
    if model is None:
        model = _default_model(for_categorize=True)

    with get_db_session(session) as db:
        rows = (
            db.query(ParserComparison)
            .join(RawDocument, ParserComparison.raw_document_id == RawDocument.id)
            .filter(RawDocument.source_id == source_id)
            .all()
        )

        if not rows:
            return {
                "comparisons_read": 0,
                "error": f"No comparisons found for source '{source_id}'. Run 'compare run' first.",
            }

        comp_dicts = [
            {
                "id": r.id,
                "parser_ids": r.parser_ids,
                "document_description": r.document_description,
                "features_present": (r.categories or {}).get("features_present"),
                "per_parser_observations": (r.categories or {}).get("per_parser_observations"),
                "comparison": r.comparison,
            }
            for r in rows
        ]
        comp_ids = [r.id for r in rows]

    prompt, prompt_template = _build_categorize_prompt(comp_dicts, prompt_extra=prompt_extra)
    system = (
        "You are an expert at evaluating document parsing pipelines. "
        "Synthesize recurring patterns across multiple document comparisons into clear, "
        "actionable categories that help users choose the right parser for their document type."
    )

    import sys
    print(
        f"  Sending {len(comp_dicts)} comparison(s) to {model} — this may take a while...",
        flush=True,
        file=sys.stderr,
    )

    t0 = time.time()
    try:
        client = _get_openai_client(model)
        categories_text = _call_llm(client, model, system, prompt)
    except Exception as e:
        logger.error(f"LLM call failed during categorization: {e}")
        return {"comparisons_read": len(comp_dicts), "error": str(e)}
    elapsed = time.time() - t0

    # Generate a short title from the output (small second call) unless user supplied one
    if not title:
        title = _generate_title(client, model, categories_text, prompt_extra)

    with get_db_session(session) as db:
        record = ParserCategories(
            id=str(uuid.uuid4()),
            source_id=source_id,
            title=title,
            categories_text=categories_text,
            model=model,
            num_comparisons=len(comp_ids),
            comparison_ids=comp_ids,
            prompt=prompt_template,
            prompt_extra=prompt_extra,
            meta={"elapsed_seconds": round(elapsed, 2)},
        )
        db.add(record)
        db.flush()
        categories_id = record.id

    logger.info(f"Stored ParserCategories {categories_id} for source={source_id} ({elapsed:.1f}s)")
    return {
        "categories_id": categories_id,
        "source_id": source_id,
        "title": title,
        "comparisons_read": len(comp_dicts),
        "categories_text": categories_text,
        "model": model,
        "elapsed_seconds": round(elapsed, 2),
    }


def list_categories(source_id: Optional[str] = None, session=None) -> List[Dict[str, Any]]:
    """List stored categorization runs, most recent first."""
    with get_db_session(session) as db:
        query = db.query(ParserCategories)
        if source_id:
            query = query.filter(ParserCategories.source_id == source_id)
        rows = query.order_by(ParserCategories.created_time.desc()).all()
        return [
            {
                "id": r.id,
                "source_id": r.source_id,
                "title": r.title,
                "model": r.model,
                "num_comparisons": r.num_comparisons,
                "prompt_extra": r.prompt_extra,
                "created_time": r.created_time,
                "meta": r.meta,
            }
            for r in rows
        ]


def get_latest_categories(source_id: str, session=None) -> Optional[Dict[str, Any]]:
    """Fetch the most recent ParserCategories run for a source."""
    with get_db_session(session) as db:
        row = (
            db.query(ParserCategories)
            .filter(ParserCategories.source_id == source_id)
            .order_by(ParserCategories.created_time.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id,
            "source_id": row.source_id,
            "title": row.title,
            "categories_text": row.categories_text,
            "model": row.model,
            "num_comparisons": row.num_comparisons,
            "comparison_ids": row.comparison_ids,
            "prompt_extra": row.prompt_extra,
            "created_time": row.created_time,
            "meta": row.meta,
        }


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def get_comparison(raw_document_id: str, session=None) -> Optional[Dict[str, Any]]:
    """Fetch the most recent ParserComparison for a raw document as a plain dict."""
    with get_db_session(session) as db:
        row = (
            db.query(ParserComparison)
            .filter(ParserComparison.raw_document_id == raw_document_id)
            .order_by(ParserComparison.created_time.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "id": row.id,
            "raw_document_id": row.raw_document_id,
            "document_ids": row.document_ids,
            "parser_ids": row.parser_ids,
            "document_description": row.document_description,
            "comparison": row.comparison,
            "categories": row.categories,
            "model": row.model,
            "created_time": row.created_time,
            "meta": row.meta,
        }


def list_comparisons(source_id: Optional[str] = None, session=None) -> List[Dict[str, Any]]:
    """List stored comparisons, optionally filtered by source."""
    with get_db_session(session) as db:
        query = db.query(ParserComparison)
        if source_id:
            query = query.join(
                RawDocument, ParserComparison.raw_document_id == RawDocument.id
            ).filter(RawDocument.source_id == source_id)
        rows = query.order_by(ParserComparison.created_time.desc()).all()
        return [
            {
                "id": r.id,
                "raw_document_id": r.raw_document_id,
                "parser_ids": r.parser_ids,
                "has_description": r.document_description is not None,
                "model": r.model,
                "created_time": r.created_time.isoformat() if r.created_time else None,
            }
            for r in rows
        ]


def export_comparisons(
    source_id: str,
    session=None,
) -> str:
    """Export all comparisons for a source as a single markdown document.

    Suitable for pasting into a frontier-model chat interface for ad-hoc analysis.

    Returns:
        Markdown string with one section per document.
    """
    with get_db_session(session) as db:
        rows = (
            db.query(ParserComparison, RawDocument)
            .join(RawDocument, ParserComparison.raw_document_id == RawDocument.id)
            .filter(RawDocument.source_id == source_id)
            .order_by(RawDocument.doc_id)
            .all()
        )
        if not rows:
            return f"# No comparisons found for source '{source_id}'\n"

        sections = [f"# Parser Comparison Export — source: {source_id}\n"]
        sections.append(f"Total documents: {len(rows)}\n")

        for cmp, raw in rows:
            doc_id = raw.doc_id or raw.id
            parsers = ", ".join(cmp.parser_ids or [])
            sections.append(f"\n---\n\n## {doc_id}\n")
            sections.append(f"**Parsers:** {parsers}  \n")
            if cmp.document_description:
                sections.append(f"**Document description:** {cmp.document_description}  \n")
            sections.append(f"\n### Comparison\n\n{cmp.comparison or '(none)'}\n")

    return "\n".join(sections)
