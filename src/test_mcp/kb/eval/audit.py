"""Question auditing and review workflows."""

import json
import logging
import os
from typing import List, Optional

from .core import EvalDataset, EvalAudit
from ..base import get
from ..database import get_db_session
from ...llm import get_openai_client

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
        >>> audit = add_audit(
        ...     question_id="abc-123",
        ...     is_valid=True,
        ...     audit_type="human_review",
        ...     auditor_name="reviewer1",
        ...     comments="Good question, clear and answerable"
        ... )
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
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
        session.commit()

        # Refresh and expunge if we own the session (to prevent DetachedInstanceError)
        if own_session:
            session.refresh(audit)
            session.expunge(audit)

        logger.info(f"Created {audit_type} audit for question {question_id}: valid={is_valid}")
        return audit

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error creating audit: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


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
        >>> audit = audit_question("abc-123")
        >>> print(audit.is_valid)  # True or False
        >>> print(audit.comments)  # LLM explanation
        >>> print(audit.meta['model'])  # Model used
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        # Get the question
        question = session.query(EvalDataset).filter_by(id=question_id).first()
        if not question:
            raise ValueError(f"Question not found: {question_id}")

        # Get source document if available
        source_document = None
        if question.source_document_id:
            source_document = get(uuid=question.source_document_id, session=session)

        # Get model
        if model is None:
            model = os.getenv('EVAL_GEN_MODEL', 'gemini-2.5-flash-lite')

        client = get_openai_client()

        # Build audit prompt
        prompt_template = """Evaluate the following evaluation question for quality:

Question: {question}
{source_context}

Assess whether this question is:
1. Clear and unambiguous
2. Answerable from the provided document (if available)
3. Well-formed and grammatically correct
4. Specific enough to have a definite answer
5. Not too broad or vague

Respond with ONLY a valid JSON object:
{{
  "is_valid": true or false,
  "comments": "Brief explanation of your decision"
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
                    "content": "You are a helpful assistant that evaluates question quality for knowledge base evaluation. Always respond with valid JSON."
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
        result = json.loads(content)

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
        session.commit()

        # Refresh and expunge if we own the session (to prevent DetachedInstanceError)
        if own_session:
            session.refresh(audit)
            session.expunge(audit)

        logger.info(f"LLM audit for question {question_id}: valid={is_valid} (model: {model})")
        return audit

    except Exception as e:
        if own_session:
            session.rollback()
        logger.error(f"Error auditing question with LLM: {e}")
        raise

    finally:
        if own_session:
            session.__exit__(None, None, None)


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
        >>> audits = get_question_audits("abc-123", is_valid=True)
        >>> for audit in audits:
        ...     print(f"{audit.auditor_name} ({audit.audit_type}): {audit.is_valid}")
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
        query = session.query(EvalAudit).filter_by(question_id=question_id)

        if is_valid is not None:
            query = query.filter_by(is_valid=is_valid)

        if audit_type:
            query = query.filter_by(audit_type=audit_type)

        audits = query.order_by(EvalAudit.created_time.desc()).all()
        
        # Refresh and expunge if we own the session
        if audits and own_session:
            for audit in audits:
                session.refresh(audit)
                session.expunge(audit)
        
        return audits

    finally:
        if own_session:
            session.__exit__(None, None, None)


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
        >>> # Get questions from a generation that haven't been LLM audited yet
        >>> questions = get_unaudited_questions(
        ...     generation_id="gen-123",
        ...     audit_type="llm_judge"
        ... )
    """
    own_session = session is None
    if own_session:
        session = get_db_session().__enter__()

    try:
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

    finally:
        if own_session:
            session.__exit__(None, None, None)
