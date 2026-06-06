"""Question auditing and review workflows."""

import json
import logging
from typing import List, Optional

from .db_models import EvalDataset, EvalAudit
from ..documents import get
from ..database import get_db_session
from ...llm import get_openai_client
from ...config import get_eval_config
from ..database import get_db_session

logger = logging.getLogger(__name__)


def add_audit(
    question_id: str,
    is_valid: bool,
    audit_type: str = "human_review",
    auditor_name: Optional[str] = None,
    comments: Optional[str] = None,
    meta: Optional[dict] = None,
    session=None,
) -> EvalAudit:
    """Add an audit record for a question.

    Args:
        question_id: ID of the question being audited
        is_valid: Whether the question is valid for evaluation
        audit_type: Type of audit ('human_review', 'llm_judge', 'similarity_threshold', 'automated_check')
        auditor_name: Optional name/identifier of the auditor
        comments: Optional comments about the audit decision
        meta: Optional metadata dict (e.g., model, prompt for LLM audits)
        session: Database session

    Returns:
        EvalAudit: Created audit record

    Example:
        ```python
        audit = add_audit(
            question_id="abc-123",
            is_valid=True,
            audit_type="human_review",
            auditor_name="reviewer1",
            comments="Good question, clear and answerable"
        )
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        # Verify question exists
        question = session.query(EvalDataset).filter_by(id=question_id).first()
        if not question:
            raise ValueError(f"Question not found: {question_id}")

        # Create audit record
        audit = EvalAudit(
            question_id=question_id,
            is_valid=is_valid,
            audit_type=audit_type,
            auditor_name=auditor_name,
            comments=comments,
            meta=meta or {},
        )
        session.add(audit)

        # Refresh and expunge if we own the session (to prevent DetachedInstanceError)
        if should_close:
            session.flush()
            session.refresh(audit)
            

        logger.info(f"Created {audit_type} audit for question {question_id}: valid={is_valid}")
        return audit


def audit_question(
    question_id: str,
    model: Optional[str] = None,
    auditor_name: str = "llm_auditor",
    session=None,
) -> EvalAudit:
    """Audit a question using an LLM.

    Similar to chATLAS extra_validation approach: uses LLM to check if question
    is clear, answerable, and well-formed. Includes source document context
    to assess answerability.

    Args:
        question_id: ID of the question to audit
        model: Optional model name (defaults to EVAL_GEN_MODEL env var)
        auditor_name: Name to record for this audit
        session: Database session

    Returns:
        EvalAudit: Created audit record with metadata including model and prompt

    Example:
        ```python
        audit = audit_question("abc-123")
        print(audit.is_valid)  # True or False
        print(audit.comments)  # LLM explanation
        print(audit.meta['model'])  # Model used
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        # Get the question
        question = session.query(EvalDataset).filter_by(id=question_id).first()
        if not question:
            raise ValueError(f"Question not found: {question_id}")

        # Get source document if available
        source_document = None
        if question.source_document_id:
            source_document = get(uid=question.source_document_id, session=session)

        # Get model
        if model is None:
            eval_config = get_eval_config()
            model = eval_config['gen_model']

        client = get_openai_client(model)

        # Build audit prompt
        prompt_template = """Evaluate the following evaluation question for use in a knowledge base retrieval benchmark for high-energy physics experiments.

Question: {question}
{source_context}

A good evaluation question must satisfy ALL of the following criteria:

1. **Self-contained**: The question makes sense without reading the source document. It must not rely on implicit context like "the dewar", "the module", "Table 3", "the klystron mentioned above", or "the device described earlier". A reader with general HEP knowledge should understand what is being asked.

2. **Externally motivated**: This is a question someone working on or studying the experiment would plausibly ask from the outside — about physics, detector design, computing systems, experimental methods, or engineering choices. It is NOT a reading-comprehension quiz on one specific document.

3. **Answerable from the knowledge base**: The answer should be findable in technical documents about the experiment (detector notes, technical reports, proceedings). It should have a specific, factual answer.

4. **Well-formed**: Clear, grammatically correct, and specific enough to have a definite answer.

Examples of GOOD questions:
- "What gas mixture is used in the Mu2e straw tracker?"
- "What is the readout scheme for the BaBar electromagnetic calorimeter?"
- "What clock frequency does the ATLAS Level-1 trigger operate at?"

Examples of BAD questions (fail self-containedness):
- "What is the maximum voltage of the forty feedthroughs in the dewar?" (assumes knowledge of which dewar)
- "What range of insertion trials is shown in Table 3?" (pure document reference)
- "What material is used for the component described in section 2.3?" (document-internal reference)

Respond with ONLY a valid JSON object:
{{
  "is_valid": true or false,
  "comments": "Brief explanation. If invalid, state which criterion fails and why."
}}"""

        # Add source context if available
        source_context = ""
        if source_document and source_document.text:
            # Truncate document if too long
            max_doc_chars = 8000
            doc_text = source_document.text[:max_doc_chars]
            if len(source_document.text) > max_doc_chars:
                doc_text += "... (truncated)"
            source_context = f"\nSource Document:\n{doc_text}"
        else:
            source_context = "\nSource Document: (not available)"

        prompt = prompt_template.format(
            question=question.question,
            source_context=source_context
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert evaluator for high-energy physics knowledge base benchmarks. Your job is to assess whether questions are suitable for retrieval evaluation — they must be self-contained, externally motivated, and not rely on implicit document context. Always respond with valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            response_format={"type": "json_object"}
        )

        # Parse response
        content = response.choices[0].message.content.strip()
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            # Strip markdown code fences if present and retry
            cleaned = content.strip("`").removeprefix("json").strip()
            try:
                result = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse audit JSON for question {question_id}: {e}\nContent: {content}")
                result = {"is_valid": False, "comments": f"Audit failed: malformed LLM response"}

        is_valid = result.get("is_valid", False)
        comments = result.get("comments", "")

        # Validate is_valid is boolean
        if not isinstance(is_valid, bool):
            logger.warning(f"Invalid is_valid type '{type(is_valid)}', defaulting to False")
            is_valid = False

        # Build metadata
        meta = {
            "model": model,
            "prompt_template": prompt_template,
            "has_source_document": source_document is not None,
        }

        # Create audit record
        audit = EvalAudit(
            question_id=question_id,
            is_valid=is_valid,
            audit_type="llm_judge",
            auditor_name=auditor_name,
            comments=comments,
            meta=meta,
        )
        session.add(audit)

        # Refresh and expunge if we own the session (to prevent DetachedInstanceError)
        if should_close:
            session.commit()
            session.refresh(audit)

        logger.info(f"LLM audit for question {question_id}: valid={is_valid} (model: {model})")
        return audit


def get_question_audits(
    question_id: str,
    is_valid: Optional[bool] = None,
    audit_type: Optional[str] = None,
    session=None,
) -> List[EvalAudit]:
    """Get all audits for a question.

    Args:
        question_id: ID of the question
        is_valid: Optional filter by validity (True/False)
        audit_type: Optional filter by audit type
        session: Database session

    Returns:
        List[EvalAudit]: List of audit records, ordered by creation time (newest first)

    Example:
        ```python
        audits = get_question_audits("abc-123", is_valid=True)
        for audit in audits:
            print(f"{audit.auditor_name} ({audit.audit_type}): {audit.is_valid}")
        ```
    """
    should_close = session is None

    with get_db_session(session) as session:
        query = session.query(EvalAudit).filter_by(question_id=question_id)

        if is_valid is not None:
            query = query.filter_by(is_valid=is_valid)

        if audit_type:
            query = query.filter_by(audit_type=audit_type)

        audits = query.order_by(EvalAudit.created_time.desc()).all()

        # Refresh and expunge if we own the session
        if audits and should_close:
            for audit in audits:
                session.refresh(audit)
                

        return audits


def get_unaudited_questions(
    generation_id: Optional[str] = None,
    auditor_name: Optional[str] = None,
    audit_type: Optional[str] = None,
    limit: Optional[int] = None,
    session=None,
) -> List[EvalDataset]:
    """Get questions that have not been audited yet.

    Args:
        generation_id: Optional filter by generation ID
        auditor_name: Optional filter to questions not audited by this specific auditor
        audit_type: Optional filter to questions without this audit type
        limit: Optional limit on number of questions to return (None for all)
        session: Database session

    Returns:
        List[EvalDataset]: List of unaudited questions

    Example:
        ```python
        # Get questions from a generation that haven't been LLM audited yet
        questions = get_unaudited_questions(
            generation_id="gen-123",
            audit_type="llm_judge"
        )
        ```
    """
    with get_db_session(session) as session:
        # Start with base query
        query = session.query(EvalDataset)

        if generation_id:
            query = query.filter_by(generation_id=generation_id)

        # Build subquery for filtering
        from sqlalchemy import select
        if auditor_name or audit_type:
            subquery = select(EvalAudit.question_id)
            if auditor_name:
                subquery = subquery.where(EvalAudit.auditor_name == auditor_name)
            if audit_type:
                subquery = subquery.where(EvalAudit.audit_type == audit_type)
            query = query.filter(~EvalDataset.id.in_(subquery))
        else:
            # Questions with no audits at all
            subquery = select(EvalAudit.question_id)
            query = query.filter(~EvalDataset.id.in_(subquery))

        # Apply limit if specified
        if limit is not None:
            query = query.limit(limit)

        questions = query.all()
        logger.info(f"Found {len(questions)} unaudited questions")
        return questions
