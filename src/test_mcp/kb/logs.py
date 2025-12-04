"""Unified logging functions for all operation logs (search, parsing, chunking)."""

from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .database import get_db_session
from .search.core import SearchLog


try:
    from .embedding.core import ParsingLog, ChunkEmbeddingLog
    EMBEDDING_LOGS_AVAILABLE = True
except ImportError:
    EMBEDDING_LOGS_AVAILABLE = False
    ParsingLog = None
    ChunkEmbeddingLog = None


def get_search_logs(
    limit: int = 10,
    offset: int = 0,
    query: Optional[str] = None,
    embedding_name: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_time_search_total: Optional[float] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get search logs, optionally filtered.
    
    Args:
        limit: Maximum number of logs to return (default: 10)
        offset: Number of logs to skip (default: 0)
        query: Optional query text to filter by (partial match)
        embedding_name: Optional embedding name to filter by
        date_from: Optional start date (ISO format string or date string)
        date_to: Optional end date (ISO format string or date string)
        min_time_search_total: Optional minimum total search time in seconds
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of search log dictionaries
    """
    
    own_session = session is None
    if own_session:
        with get_db_session() as session:
            return _get_search_logs_impl(session, limit, offset, query, embedding_name, date_from, date_to, min_time_search_total)
    else:
        return _get_search_logs_impl(session, limit, offset, query, embedding_name, date_from, date_to, min_time_search_total)


def _get_search_logs_impl(
    session: Session,
    limit: int,
    offset: int,
    query: Optional[str],
    embedding_name: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    min_time_search_total: Optional[float],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_search_logs."""
    from datetime import timedelta
    
    query_obj = session.query(SearchLog)
    
    # Apply filters
    if query:
        query_obj = query_obj.filter(SearchLog.query.contains(query))
    if embedding_name:
        query_obj = query_obj.filter(SearchLog.embedding_name == embedding_name)
    
    # Date range filters
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            query_obj = query_obj.filter(SearchLog.created_time >= date_from_dt)
        except (ValueError, AttributeError):
            # Try parsing as simple date string
            try:
                date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
                query_obj = query_obj.filter(SearchLog.created_time >= date_from_dt)
            except ValueError:
                pass  # Skip invalid date
    
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            # Add one day to include the entire end date
            date_to_dt = date_to_dt + timedelta(days=1)
            query_obj = query_obj.filter(SearchLog.created_time < date_to_dt)
        except (ValueError, AttributeError):
            # Try parsing as simple date string
            try:
                date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
                date_to_dt = date_to_dt + timedelta(days=1)
                query_obj = query_obj.filter(SearchLog.created_time < date_to_dt)
            except ValueError:
                pass  # Skip invalid date
    
    # Minimum search time filter
    if min_time_search_total is not None:
        query_obj = query_obj.filter(SearchLog.time_search_total >= min_time_search_total)
    
    # Order by most recent first
    query_obj = query_obj.order_by(desc(SearchLog.created_time))
    
    # Apply limit and offset
    logs = query_obj.limit(limit).offset(offset).all()
    
    # Convert to dictionaries
    return [log.to_dict() for log in logs]


def get_parsing_logs(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get parsing logs for a document.
    
    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs to return
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of parsing log dictionaries
    """
    if not EMBEDDING_LOGS_AVAILABLE or ParsingLog is None:
        return []
    
    own_session = session is None
    if own_session:
        with get_db_session() as session:
            return _get_parsing_logs_impl(session, document_id, limit)
    else:
        return _get_parsing_logs_impl(session, document_id, limit)


def _get_parsing_logs_impl(
    session: Session,
    document_id: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_parsing_logs."""
    query = session.query(ParsingLog).filter(
        ParsingLog.document_id == document_id
    ).order_by(desc(ParsingLog.insertion_time))
    
    if limit:
        query = query.limit(limit)
    
    logs = query.all()
    
    # Convert to dictionaries
    result = []
    for log in logs:
        # Get file_path and source_type from document if available
        file_path = None
        source_type = None
        if log.document:
            source_type = log.document.source_type
            if log.document.uri:
                file_path = log.document.uri
            elif log.document.meta and isinstance(log.document.meta, dict):
                file_path = log.document.meta.get("file_path")
        
        result.append({
            "id": log.id,
            "document_id": log.document_id,
            "insertion_time": log.insertion_time.isoformat() if log.insertion_time else None,
            "text_extraction_time_seconds": log.text_extraction_time_seconds,
            "image_description_time_seconds": log.image_description_time_seconds,
            "total_time_seconds": log.total_time_seconds,
            "file_path": file_path,
            "source_type": source_type,
            "num_documents": log.num_documents,
            "text_length": log.text_length,
            "hostname": log.hostname,
            "meta": log.meta if log.meta else {},
        })
    
    return result


def get_chunking_logs(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get chunking/embedding logs for a document.
    
    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs to return
        session: Optional database session. If None, creates a new session.
    
    Returns:
        List of chunking log dictionaries
    """
    if not EMBEDDING_LOGS_AVAILABLE or ChunkEmbeddingLog is None:
        return []
    
    own_session = session is None
    if own_session:
        with get_db_session() as session:
            return _get_chunking_logs_impl(session, document_id, limit)
    else:
        return _get_chunking_logs_impl(session, document_id, limit)


def _get_chunking_logs_impl(
    session: Session,
    document_id: str,
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Internal implementation of get_chunking_logs."""
    query = session.query(ChunkEmbeddingLog).filter(
        ChunkEmbeddingLog.document_id == document_id
    ).order_by(desc(ChunkEmbeddingLog.insertion_time))
    
    if limit:
        query = query.limit(limit)
    
    logs = query.all()
    
    # Convert to dictionaries
    return [{
        "id": log.id,
        "document_id": log.document_id,
        "insertion_time": log.insertion_time.isoformat() if log.insertion_time else None,
        "chunking_time_seconds": log.chunking_time_seconds,
        "embedding_time_seconds": log.embedding_time_seconds,
        "total_time_seconds": log.total_time_seconds,
        "num_chunks": log.num_chunks,
        "num_embeddings": log.num_embeddings,
        "chunk_strategy": log.chunk_strategy,
        "embedding_name": log.embedding_name,
        "hostname": log.hostname,
        "meta": log.meta if log.meta else {},
    } for log in logs]


def get_all_logs_for_document(
    document_id: str,
    limit: Optional[int] = None,
    session: Optional[Session] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Get all logs (parsing, chunking) for a document.
    
    Args:
        document_id: Document ID to get logs for
        limit: Optional maximum number of logs per type to return
        session: Optional database session. If None, creates a new session.
    
    Returns:
        Dictionary with keys: "parsing", "chunking", "search"
        Each contains a list of log dictionaries
    """
    own_session = session is None
    if own_session:
        with get_db_session() as session:
            return {
                "parsing": get_parsing_logs(document_id, limit=limit, session=session),
                "chunking": get_chunking_logs(document_id, limit=limit, session=session),
                "search": [],  # Search logs are not per-document
            }
    else:
        return {
            "parsing": get_parsing_logs(document_id, limit=limit, session=session),
            "chunking": get_chunking_logs(document_id, limit=limit, session=session),
            "search": [],  # Search logs are not per-document
        }

