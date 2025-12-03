"""Functions for querying search logs."""

from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .core import SearchLog
from ..database import get_db_session


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
    from datetime import datetime
    
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
            from datetime import timedelta
            date_to_dt = date_to_dt + timedelta(days=1)
            query_obj = query_obj.filter(SearchLog.created_time < date_to_dt)
        except (ValueError, AttributeError):
            # Try parsing as simple date string
            try:
                date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
                from datetime import timedelta
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