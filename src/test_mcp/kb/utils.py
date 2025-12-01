"""Utility functions for knowledge base operations."""

from typing import Dict, Any, List
from .database import get_db_session
from .core import Document, Source


def get_stats() -> Dict[str, Any]:
    """Get knowledge base statistics.
    
    Returns:
        Dictionary with statistics:
        - total_documents: int
        - total_sources: int
        - documents_by_source: List[Dict[str, Any]] with source_id and count
    """
    with get_db_session() as session:
        # Count documents
        doc_count = session.query(Document).count()
        
        # Count sources
        source_count = session.query(Source).count()
        
        # Count by source
        from sqlalchemy import func
        docs_by_source = (
            session.query(Document.source_id, func.count(Document.id))
            .group_by(Document.source_id)
            .all()
        )
    
    return {
        "total_documents": doc_count,
        "total_sources": source_count,
        "documents_by_source": [
            {"source_id": source_id, "count": count}
            for source_id, count in docs_by_source
        ]
    }


def list_sources() -> List[Dict[str, Any]]:
    """List all sources in the knowledge base.
    
    Returns:
        List of dictionaries with source information:
        - id: str
        - name: str | None
        - description: str | None
        - base_uri: str | None
        - created_at: str | None (ISO format)
    """
    with get_db_session() as session:
        sources = session.query(Source).order_by(Source.id).all()
        
        # Access all attributes while session is still open
        result = []
        for source in sources:
            result.append({
                "id": source.id,
                "name": source.name,
                "description": source.description,
                "base_uri": source.base_uri,
                "created_at": source.created_time.isoformat() if source.created_time else None,
            })
    
    return result

