"""Database models for evaluation and benchmarking."""

import hashlib
import json
import uuid
from typing import Dict, List, Optional, Union

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship, Session

from ..db_models import Base
from ..database import get_db_session
from ..database import get_db_session


class EvalGeneration(Base):
    """Table 'eval_generation' for metadata about how evaluation questions were generated.

    Attributes:
        id (str): Primary key (UUID stored as string).
        name (str): Optional user-friendly name.
        generation_type (str): 'synthetic', 'human', 'source'.
        generation_method (str): 'keypoint', 'persona', etc.
        prompt (str): Prompt template used. Optional.
        source_id (str): Foreign key to the sources table. Optional.
        source_type (str): 'text', 'image', 'mixed'. Optional.
        source_filters (dict): Filters applied when selecting documents. Optional.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        content_hash (str): hash used for deduplication of generations.
        created_time (datetime): Timestamp when the generation was created.
    """

    __tablename__ = "eval_generation"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Generation metadata
    name = Column(String(256), nullable=True, index=True)  # Optional user-friendly name
    generation_type = Column(String(32), nullable=False, index=True)  # 'synthetic', 'human', 'source'
    generation_method = Column(String(64), nullable=True, index=True)  # 'keypoint', 'persona', etc.
    prompt = Column(Text, nullable=True)  # Prompt template used

    # Source information - which source the documents came from
    source_id = Column(String(256), nullable=True, index=True)  # e.g., "mu2e-docdb"
    source_type = Column(String(64), nullable=False, default="text", index=True)
    source_filters = Column(JSON, nullable=True)  # Filters applied when selecting documents

    # Metadata (can include model, specific document IDs used, etc.)
    meta = Column(JSON, nullable=True, default=dict)

    # Content hash for deduplication (SHA256 of all identifying fields)
    # Allows database-level uniqueness checking even with JSON meta field
    content_hash = Column(String(64), nullable=True, index=True, unique=True)

    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    questions = relationship("EvalDataset", back_populates="generation")

    # Indexes
    __table_args__ = (
        Index("idx_eval_generation_type", "generation_type"),
        Index("idx_eval_generation_method", "generation_method"),
        Index("idx_eval_generation_created_time", "created_time"),
        Index("idx_eval_generation_source_id", "source_id"),
    )

    def __repr__(self) -> str:
        # Don't access lazy-loaded relationships in __repr__ to avoid DetachedInstanceError
        name_str = f"name={self.name}, " if self.name else ""
        return (
            f"<EvalGeneration(id={self.id}, {name_str}type={self.generation_type}, "
            f"method={self.generation_method}, source_id={self.source_id})>"
        )


class EvalDataset(Base):
    """Table `eval_dataset` with evaluation questions/queries.

    Attributes:
        id (str): Primary key (UUID stored as string).
        question (str): The question/query.
        generation_id (str): Foreign key to the eval_generation table. Optional.
        source_document_id (str): Foreign key to the documents table if applicable. Optional.
        answer (str): Expected answer if available. Most evaluations don't require this. Optional.
        keypoints (str): Key facts/snippets question is based on if applicable. Can help the evaluation. Optional.
        generation_time_seconds (float): Time for generation in seconds. Optional.
        hostname (str): Hostname of the machine where question was generated. Optional.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        created_time (datetime): Timestamp when the question was created.
    """

    __tablename__ = "eval_dataset"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Question data
    question = Column(Text, nullable=False)

    # Provenance
    generation_id = Column(
        String(36),
        ForeignKey("eval_generation.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Optional fields
    answer = Column(Text, nullable=True)  # Expected answer (for future LLM-as-judge)
    keypoints = Column(Text, nullable=True)  # Key facts/snippets question is based on

    # Timing (for LLM-generated questions)
    generation_time_seconds = Column(Float, nullable=True)
    hostname = Column(String(256), nullable=True, index=True)  # Where question was generated

    # Metadata
    meta = Column(JSON, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    generation = relationship("EvalGeneration", back_populates="questions")
    source_document = relationship("Document", backref="eval_questions")
    audits = relationship("EvalAudit", back_populates="question", cascade="all, delete-orphan")
    results = relationship("EvalResult", back_populates="question", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_eval_dataset_generation_id", "generation_id"),
        Index("idx_eval_dataset_source_document_id", "source_document_id"),
        Index("idx_eval_dataset_created_time", "created_time"),
        Index("idx_eval_dataset_hostname", "hostname"),
    )

    def __repr__(self) -> str:
        question_preview = self.question[:50] + "..." if len(self.question) > 50 else self.question
        return f"<EvalDataset(id={self.id}, question='{question_preview}', source_doc={self.source_document_id})>"


class EvalAudit(Base):
    """Table 'eval_audit' for audit/review records for evaluation questions. Note that the uses of this additonal filter step is optional.
    
    Attributes:
        id (str): Primary key (UUID stored as string).
        question_id (str): Foreign key to the eval_dataset table.
        is_valid (bool): Key flag, True if question is valid for evaluation under this audit/review filter.
        audit_type (str): 'human_review', 'llm_judge', 'similarity_threshold', 'automated_check'. Optional.
        comments (str): Comments on the audit/review. Optional.
        auditor_name (str): Username or model name. Optional.
        score (float): Confidence score (0.0-1.0). Not used. Optional.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        created_time (datetime): Timestamp when the audit/review was created.
    """

    __tablename__ = "eval_audit"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Link to question
    question_id = Column(
        String(36),
        ForeignKey("eval_dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Audit results
    is_valid = Column(Boolean, nullable=False, index=True)  # True if question is valid for evaluation
    audit_type = Column(String(64), nullable=False, index=True)  # 'human_review', 'llm_judge', 'similarity_threshold', 'automated_check'
    comments = Column(Text, nullable=True)
    auditor_name = Column(String(128), nullable=True, index=True)  # Username or model name
    score = Column(Float, nullable=True)  # Optional 0.0-1.0 confidence

    # Metadata
    meta = Column(JSON, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    question = relationship("EvalDataset", back_populates="audits")

    # Indexes
    __table_args__ = (
        Index("idx_eval_audit_question_id", "question_id"),
        Index("idx_eval_audit_is_valid", "is_valid"),
        Index("idx_eval_audit_type", "audit_type"),
        Index("idx_eval_audit_created_time", "created_time"),
        # Composite index for filtering questions by audit status
        Index("idx_eval_audit_question_valid", "question_id", "is_valid"),
    )

    def __repr__(self) -> str:
        return (
            f"<EvalAudit(id={self.id}, question_id={self.question_id}, "
            f"is_valid={self.is_valid}, type={self.audit_type})>"
        )


class EvalRun(Base):
    """Table 'eval_runs' to store configuration for an evaluation run/experiment.
    
    Attributes:
        id (str): Primary key (UUID stored as string).
        name (str): Optional user-friendly name. Optional.
        description (str): Optional description of the evaluation run. Optional. TODO Needed?
        generation_id (str): Foreign key to the eval_generation table. Optional.
        audit_filters (dict): Filters applied when selecting questions. Optional.
        embedding_name (str): Foreign key to the embedding_configs table if this filter is used for a run. Optional.
        chunking_strategy (str): Foreign key to the chunk_strategies if this filter is used for a run. Optional.
        max_results (int): Maximum number of results to return used for the quueris in this run. Optional.
        search_filters (dict): Filters applied when searching for documents. Optional.
        judge_strategy (dict): Strategy for judging the evaluation results. Optional.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        created_time (datetime): Timestamp when the evaluation run was created.
    """

    __tablename__ = "eval_runs"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Run metadata
    name = Column(String(256), nullable=True)
    description = Column(Text, nullable=True)

    # Question selection
    generation_id = Column(
        String(36),
        ForeignKey("eval_generation.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    audit_filters = Column(JSON, nullable=True)  # e.g., {"verdict": "approved", "audit_type": "human_review"}

    # Search configuration
    embedding_name = Column(String(64), nullable=True, index=True)
    chunking_strategy = Column(String(128), nullable=True, index=True)
    max_results = Column(Integer, nullable=False, default=10)
    search_filters = Column(JSON, nullable=True)  # Document filters

    # Judge strategy
    judge_strategy = Column(JSON, nullable=True)  # Flexible for future expansion

    # Metadata
    meta = Column(JSON, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    generation = relationship("EvalGeneration", backref="eval_runs")
    results = relationship("EvalResult", back_populates="run", cascade="all, delete-orphan")

    # Indexes
    __table_args__ = (
        Index("idx_eval_runs_generation_id", "generation_id"),
        Index("idx_eval_runs_embedding_name", "embedding_name"),
        Index("idx_eval_runs_created_time", "created_time"),
    )

    def __repr__(self) -> str:
        # Don't access lazy-loaded relationships in __repr__ to avoid DetachedInstanceError
        return (
            f"<EvalRun(id={self.id}, name={self.name}, "
            f"embedding={self.embedding_name})>"
        )


class EvalResult(Base):
    """Table `eval_results` for individual evaluation result for a question in a run, linked to a specific run/experiment.

    Attributes:
        id (str): Primary key (UUID stored as string).
        run_id (str): Foreign key to the eval_runs table that holds the run configuration for this result.
        question_id (str): Foreign key to the eval_dataset table that holds the question that was evaluated.
        is_hit (bool): Key flag, True if the ground truth document was found in the retrieval results.
        hit_rank (int): Rank of the ground truth document that was hit. Null if not found within max_results.
        is_judge_hit (bool): Key flag, True if the question was judged to be asnswerable with the retrieved information.
        justification (str): LLM judge explanation. Optional.
        judge_time_seconds (float): Time for LLM judgment in seconds. Optional.
        retrieval_time_seconds (float): Time for retrieval in seconds. Optional.
        hostname (str): Hostname of the machine where evaluation ran. Optional.
        best_similarity (float): Best similarity score across all retrieved documents. Optional.
        meta (dict): Metadata - flexible JSON field for additional metadata.
        created_time (datetime): Timestamp when the evaluation result was created.
    """

    __tablename__ = "eval_results"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Links
    run_id = Column(
        String(36),
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question_id = Column(
        String(36),
        ForeignKey("eval_dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Hit metrics
    is_hit = Column(Boolean, nullable=False, index=True)  # Exact document match
    hit_rank = Column(Integer, nullable=True, index=True)  # null if not found

    # LLM judge metrics (if judge_strategy configured)
    is_judge_hit = Column(Boolean, nullable=True, index=True)  # LLM assessment of answer quality
    justification = Column(Text, nullable=True)  # LLM judge explanation
    judge_time_seconds = Column(Float, nullable=True)  # Time for LLM judgment

    # Timing
    retrieval_time_seconds = Column(Float, nullable=True)

    # Execution metadata
    hostname = Column(String(256), nullable=True, index=True)  # Where evaluation ran

    # Optional fields
    best_similarity = Column(Float, nullable=True)

    # Metadata
    meta = Column(JSON, nullable=True, default=dict)
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        index=True,
    )

    # Relationships
    run = relationship("EvalRun", back_populates="results")
    question = relationship("EvalDataset", back_populates="results")
    retrieved_docs = relationship(
        "EvalRetrievedDocument",
        back_populates="result",
        cascade="all, delete-orphan",
        order_by="EvalRetrievedDocument.rank"
    )

    # Indexes
    __table_args__ = (
        Index("idx_eval_results_run_id", "run_id"),
        Index("idx_eval_results_question_id", "question_id"),
        Index("idx_eval_results_is_hit", "is_hit"),
        Index("idx_eval_results_is_judge_hit", "is_judge_hit"),
        Index("idx_eval_results_hit_rank", "hit_rank"),
        Index("idx_eval_results_hostname", "hostname"),
        # Composite indexes for efficient aggregation
        Index("idx_eval_results_run_hit", "run_id", "is_hit"),
        Index("idx_eval_results_run_judge_hit", "run_id", "is_judge_hit"),
        # Unique constraint to prevent duplicate results
        UniqueConstraint("run_id", "question_id", name="uq_eval_result_run_question"),
    )

    def __repr__(self) -> str:
        return (
            f"<EvalResult(id={self.id}, run_id={self.run_id}, "
            f"is_hit={self.is_hit}, hit_rank={self.hit_rank})>"
        )


class EvalRetrievedDocument(Base):
    """Table `eval_retrieved_documents` to store details of retrived documents linked to a eval_result. This table is intended for debugging and enhanced analysis of the evaluation results.
    
    Attributes:
        id (str): Primary key (UUID stored as string).
        result_id (str): Foreign key to the eval_results table that holds the result this recoed is linked to.
        document_id (str): Foreign key to the documents table that holds the document that was retrieved.
        rank (int): Rank of the document that was retrieved.
        similarity (float): Similarity score of the document that was retrieved. Optional.
        chunk_ids (list): List of chunk IDs that contributed to this result. Optional.
        created_time (datetime): Timestamp when the retrieved document was created.
    """

    __tablename__ = "eval_retrieved_documents"

    # Primary key
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )

    # Links
    result_id = Column(
        String(36),
        ForeignKey("eval_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Retrieval data
    rank = Column(Integer, nullable=False, index=True)  # 1-based
    similarity = Column(Float, nullable=True)
    chunk_ids = Column(JSON, nullable=True)  # Array of chunk IDs that contributed to this result

    # Timestamp
    created_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )

    # Relationships
    result = relationship("EvalResult", back_populates="retrieved_docs")
    document = relationship("Document", backref="eval_retrievals")

    # Indexes
    __table_args__ = (
        Index("idx_eval_retrieved_docs_result_id", "result_id"),
        Index("idx_eval_retrieved_docs_document_id", "document_id"),
        Index("idx_eval_retrieved_docs_rank", "rank"),
        # Unique constraint to prevent duplicate ranks within a result
        UniqueConstraint("result_id", "rank", name="uq_eval_retrieved_doc_result_rank"),
    )

    def __repr__(self) -> str:
        return (
            f"<EvalRetrievedDocument(id={self.id}, result_id={self.result_id}, "
            f"document_id={self.document_id}, rank={self.rank}, similarity={self.similarity})>"
        )


# Helper functions

def compute_generation_hash(
    generation_type: str,
    generation_method: str,
    source_id: Optional[str],
    source_type: str,
    prompt: Optional[str],
    meta: Optional[Dict],
) -> str:
    """Compute deterministic SHA256 hash of generation identifying fields.
    
    Note: Only includes meta.personas (if present), not the full meta dict.
    Prompt is excluded from hash as it may vary without changing generation identity.
    """
    # Extract only personas from meta for hashing
    meta_for_hash = {}
    if meta and "personas" in meta:
        meta_for_hash["personas"] = meta["personas"]
    
    hash_data = {
        "generation_type": generation_type,
        "generation_method": generation_method,
        "source_id": source_id,
        "source_type": source_type,
        "meta": meta_for_hash,
    }
    hash_string = json.dumps(hash_data, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(hash_string.encode('utf-8')).hexdigest()


def get_or_create_eval_generation(
    generation_type: str,
    generation_method: str,
    source_id: Optional[str] = None,
    source_type: str = "text",
    prompt: Optional[str] = None,
    meta: Optional[Dict] = None,
    session: Optional[Session] = None,
) -> EvalGeneration:
    """Get existing or create new EvalGeneration using database-intrinsic content hash."""
    should_close = session is None

    with get_db_session(session) as session:
        # Compute content hash from all identifying fields
        content_hash = compute_generation_hash(
            generation_type, generation_method, source_id, source_type, prompt, meta or {}
        )

        # Query by hash (uses unique index)
        existing = session.query(EvalGeneration).filter_by(content_hash=content_hash).first()

        if existing:
            if should_close:
                session.refresh(existing)
                
            return existing

        # Create new
        generation = EvalGeneration(
            generation_type=generation_type,
            generation_method=generation_method,
            source_id=source_id,
            source_type=source_type,
            prompt=prompt,
            meta=meta or {},
            content_hash=content_hash,
        )
        session.add(generation)

        if should_close:
            session.flush()
            session.refresh(generation)
            
        else:
            session.flush()

        return generation


def get_eval_generation(
    generation_id: Optional[str] = None,
    generation_type: Optional[str] = None,
    source_id: Optional[str] = None,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> Union[EvalGeneration, List[EvalGeneration], None]:
    """Get evaluation generation(s).

    Args:
        generation_id: Optional generation ID to retrieve specific generation
        generation_type: Optional filter by generation type ('synthetic', 'human', 'source')
        source_id: Optional filter by source
        limit: Optional limit on number of results
        session: Optional database session

    Returns:
        Single EvalGeneration if generation_id provided, list otherwise, or None if not found
    """
    should_close = session is None

    with get_db_session(session) as session:
        query = session.query(EvalGeneration)

        if generation_id:
            result = query.filter(EvalGeneration.id == generation_id).first()
            if result and should_close:
                session.refresh(result)
                
            return result

        if generation_type:
            query = query.filter(EvalGeneration.generation_type == generation_type)

        if source_id:
            query = query.filter(EvalGeneration.source_id == source_id)

        query = query.order_by(EvalGeneration.created_time.desc())

        if limit:
            query = query.limit(limit)

        results = query.all()
        if results and should_close:
            for result in results:
                session.refresh(result)
                
        return results


def get_eval_questions(
    question_id: Optional[str] = None,
    generation_id: Optional[str] = None,
    source_document_id: Optional[str] = None,
    audit_filter: Optional[Dict] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    session: Optional[Session] = None,
) -> Union[EvalDataset, List[EvalDataset], None]:
    """Get evaluation question(s) with optional audit filtering.

    Args:
        question_id: Optional question ID to retrieve specific question
        generation_id: Optional filter by generation
        source_document_id: Optional filter by source document
        audit_filter: Optional audit criteria (e.g., {"is_valid": True, "audit_type": "human_review"})
        limit: Optional limit on number of results
        offset: Optional offset for pagination
        session: Optional database session

    Returns:
        Single EvalDataset if question_id provided, list otherwise, or None if not found
    """
    should_close = session is None

    with get_db_session(session) as session:
        query = session.query(EvalDataset)

        if question_id:
            result = query.filter(EvalDataset.id == question_id).first()
            if result and should_close:
                session.refresh(result)
                
            return result

        if generation_id:
            query = query.filter(EvalDataset.generation_id == generation_id)

        if source_document_id:
            query = query.filter(EvalDataset.source_document_id == source_document_id)

        # Apply audit filter if provided
        if audit_filter:
            # Join with audit table and filter
            is_valid = audit_filter.get("is_valid")
            audit_type = audit_filter.get("audit_type")

            query = query.join(EvalAudit)

            if is_valid is not None:
                query = query.filter(EvalAudit.is_valid == is_valid)
            if audit_type:
                query = query.filter(EvalAudit.audit_type == audit_type)

            # Remove duplicates if multiple audits match
            query = query.distinct()

        query = query.order_by(EvalDataset.created_time.desc())

        if offset:
            query = query.offset(offset)

        if limit:
            query = query.limit(limit)

        results = query.all()
        if results and should_close:
            for result in results:
                session.refresh(result)
                
        return results


def get_eval_run(
    run_id: Optional[str] = None,
    name: Optional[str] = None,
    generation_id: Optional[str] = None,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> Union[EvalRun, List[EvalRun], None]:
    """Get evaluation run(s).

    Args:
        run_id: Optional run ID to retrieve specific run
        name: Optional filter by name (partial match)
        generation_id: Optional filter by generation ID
        limit: Optional limit on number of results
        session: Optional database session

    Returns:
        Single EvalRun if run_id provided, list otherwise, or None if not found
    """
    should_close = session is None

    with get_db_session(session) as session:
        query = session.query(EvalRun)

        if run_id:
            result = query.filter(EvalRun.id == run_id).first()
            if result and should_close:
                session.refresh(result)
                
            return result

        if name:
            query = query.filter(EvalRun.name.like(f"%{name}%"))
        
        if generation_id:
            query = query.filter(EvalRun.generation_id == generation_id)

        query = query.order_by(EvalRun.created_time.desc())

        if limit:
            query = query.limit(limit)

        results = query.all()
        if results and should_close:
            for result in results:
                session.refresh(result)
                
        return results
