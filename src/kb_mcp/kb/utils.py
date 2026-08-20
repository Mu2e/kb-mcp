"""Utility functions for knowledge base operations."""

import hashlib
import logging
from typing import Dict, Any, List, Tuple, Optional
from .database import get_db_session
from .db_models import Document, Source
from .documents import get

logger = logging.getLogger(__name__)

from .embedding import chunk_and_embed, get_chunks


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

        from sqlalchemy import func
        # Count by source
        docs_by_source = (
            session.query(Document.source_id, func.count(Document.id))
            .group_by(Document.source_id)
            .all()
        )

        # Count raw documents
        from .db_models import RawDocument
        raw_count = session.query(RawDocument).count()
        raw_by_source = (
            session.query(RawDocument.source_id, func.count(RawDocument.id))
            .group_by(RawDocument.source_id)
            .all()
        )

        # Count by source + parser + doc_type (drives the unified table)
        docs_by_source_parser_type = (
            session.query(Document.source_id, Document.parser_id, Document.doc_type, func.count(Document.id))
            .group_by(Document.source_id, Document.parser_id, Document.doc_type)
            .order_by(Document.source_id, Document.parser_id, Document.doc_type)
            .all()
        )

    return {
        "total_documents": doc_count,
        "total_sources": source_count,
        "documents_by_source": [
            {"source_id": source_id, "count": count}
            for source_id, count in docs_by_source
        ],
        "total_raw_documents": raw_count,
        "raw_documents_by_source": [
            {"source_id": source_id, "count": count}
            for source_id, count in raw_by_source
        ],
        "documents_by_source_parser_type": [
            {"source_id": source_id, "parser_id": parser_id or "unknown", "doc_type": doc_type or "unknown", "count": count}
            for source_id, parser_id, doc_type, count in docs_by_source_parser_type
        ],
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


def find_all_duplicates(
    by_hash: bool = True,
    by_id: bool = True,
) -> List[Tuple[str, List[str], List[str]]]:
    """Find all duplicate documents in the database using efficient SQL queries.
    
    Args:
        by_hash: If True, find duplicates by content_hash
        by_id: If True, find duplicates by (source_id, doc_id)
    
    Returns:
        List of tuples: (keep_id, by_id_duplicates, by_hash_duplicates)
        - keep_id: UUID of document to keep (oldest by insert_time)
        - by_id_duplicates: List of UUIDs that are duplicates by source_id+doc_id
        - by_hash_duplicates: List of UUIDs that are duplicates by content_hash
    """
    from sqlalchemy import func
    from collections import defaultdict
    
    # Map keep_id -> (by_id_duplicates, by_hash_duplicates)
    duplicate_groups = defaultdict(lambda: ([], []))
    processed_keep_ids = set()  # Track which documents are "keep" documents
    
    with get_db_session() as session:
        # First, ensure all documents have content_hash computed
        docs_without_hash = (
            session.query(Document)
            .filter(Document.content_hash.is_(None))
            .all()
        )
        
        for doc in docs_without_hash:
            if doc.text:
                content = doc.text.encode("utf-8")
            elif doc.binary:
                content = doc.binary
            else:
                content = b""
            if content:
                doc.content_hash = hashlib.sha256(content).hexdigest()
        
        if docs_without_hash:
            session.commit()
        
        # Find duplicates by (source_id, doc_id, parser_id)
        # parser_id is part of the identity key — same doc parsed by different parsers is intentional
        if by_id:
            id_duplicates = (
                session.query(
                    Document.source_id,
                    Document.doc_id,
                    Document.parser_id,
                    func.count(Document.id).label('count')
                )
                .filter(Document.source_id.isnot(None), Document.doc_id.isnot(None))
                .group_by(Document.source_id, Document.doc_id, Document.parser_id)
                .having(func.count(Document.id) > 1)
                .all()
            )

            for source_id, doc_id, parser_id, count in id_duplicates:
                # Get all documents with this source_id+doc_id+parser_id
                docs = (
                    session.query(Document)
                    .filter(
                        Document.source_id == source_id,
                        Document.doc_id == doc_id,
                        Document.parser_id == parser_id,
                    )
                    .order_by(Document.insert_time)
                    .all()
                )
                
                if len(docs) > 1:
                    keep_id = docs[0].id
                    duplicate_ids = [doc.id for doc in docs[1:]]
                    duplicate_groups[keep_id][0].extend(duplicate_ids)
                    processed_keep_ids.add(keep_id)
        
        # Find duplicates by (content_hash, parser_id)
        # Same content from different parsers is intentional, not a duplicate
        if by_hash:
            hash_duplicates = (
                session.query(
                    Document.content_hash,
                    Document.parser_id,
                    func.count(Document.id).label('count')
                )
                .filter(Document.content_hash.isnot(None))
                .group_by(Document.content_hash, Document.parser_id)
                .having(func.count(Document.id) > 1)
                .all()
            )

            for content_hash, parser_id, count in hash_duplicates:
                # Get all documents with this content_hash + parser_id
                docs = (
                    session.query(Document)
                    .filter(
                        Document.content_hash == content_hash,
                        Document.parser_id == parser_id,
                    )
                    .order_by(Document.insert_time)
                    .all()
                )
                
                if len(docs) > 1:
                    keep_id = docs[0].id
                    duplicate_ids = [doc.id for doc in docs[1:] if doc.id not in processed_keep_ids]
                    if duplicate_ids:
                        duplicate_groups[keep_id][1].extend(duplicate_ids)
                        if keep_id not in processed_keep_ids:
                            processed_keep_ids.add(keep_id)
    
    # Convert to list of tuples
    return [
        (keep_id, by_id_dups, by_hash_dups)
        for keep_id, (by_id_dups, by_hash_dups) in duplicate_groups.items()
        if by_id_dups or by_hash_dups  # Only include if there are actual duplicates
    ]


def get_metadata_keys(session=None, limit: int = 1000) -> List[str]:
    """
    Get all unique metadata keys from documents.
    
    This function extracts all distinct keys from the `meta` JSON field
    across all documents in the database. It uses efficient database
    queries for both PostgreSQL and SQLite.
    
    Note: For better performance with large databases, consider implementing
    a cache table that stores metadata keys and is updated when documents
    are added/updated. This would avoid scanning documents on each call.
    
    Args:
        session: Optional database session. If not provided, creates a new one.
        limit: Maximum number of documents to scan for SQLite (PostgreSQL uses
               efficient JSON operators and doesn't need this limit).
    
    Returns:
        List of unique metadata keys, sorted alphabetically.
    
    Examples:
        ```python
        keys = get_metadata_keys()
        print(keys)
        # Returns: ['author', 'category', 'date', 'title']
        ```
    """
    with get_db_session(session) as session:
        dialect_name = session.bind.dialect.name if session.bind else None
        all_keys = set()
        
        if dialect_name == "postgresql":
            # PostgreSQL: use jsonb_object_keys for efficient extraction
            from sqlalchemy import text
            result = session.execute(text("""
                SELECT DISTINCT jsonb_object_keys(meta) as key
                FROM documents
                WHERE meta IS NOT NULL AND meta != '{}'::jsonb
            """))
            all_keys = {row[0] for row in result}
        else:
            # SQLite: query documents and extract keys
            documents = session.query(Document).filter(
                Document.meta.isnot(None)
            ).limit(limit).all()
            
            for doc in documents:
                if doc.meta and isinstance(doc.meta, dict):
                    all_keys.update(doc.meta.keys())
        
        # Sort keys alphabetically
        sorted_keys = sorted(list(all_keys))

        # Prepend direct Document columns (title, title_gen, doc_id) so they're
        # selectable alongside JSON meta keys, even though they aren't part of `meta`.
        from .search.filters import DIRECT_COLUMNS
        direct_keys = sorted(DIRECT_COLUMNS - {"source_id", "doc_type"})
        return direct_keys + sorted_keys
    


def deduplicate(
    by_hash: bool = True,
    by_id: bool = True,
) -> Dict[str, Any]:
    """Deduplicate the entire database.
    
    For each duplicate group, keeps the oldest document (by insert_time) and deletes the rest.
    
    Args:
        by_hash: If True, deduplicate by content_hash
        by_id: If True, deduplicate by (source_id, doc_id)
    
    Returns:
        Dictionary with deduplication results:
        - duplicates_found: int - Number of duplicate groups found
        - deleted: int - Number of duplicate documents deleted
        - by_id_count: int - Number of duplicates by source_id+doc_id
        - by_hash_count: int - Number of duplicates by content_hash
    """
    # Find all duplicates
    duplicates = find_all_duplicates(by_hash=by_hash, by_id=by_id)
    
    if not duplicates:
        return {
            "duplicates_found": 0,
            "deleted": 0,
            "by_id_count": 0,
            "by_hash_count": 0,
        }
    
    # Count by type
    by_id_count = sum(len(by_id_dups) for _, by_id_dups, _ in duplicates)
    by_hash_count = sum(len(by_hash_dups) for _, _, by_hash_dups in duplicates)
    
    # Apply deduplication: delete all duplicate documents (keep the oldest ones)
    deleted_count = 0
    processed_ids = set()
    
    with get_db_session() as session:
        for keep_id, by_id_dups, by_hash_dups in duplicates:
            # Mark keep_id as processed so we don't delete it
            processed_ids.add(keep_id)
            
            # Delete duplicates by source_id+doc_id
            for dup_id in by_id_dups:
                if dup_id not in processed_ids:
                    doc = session.query(Document).filter(Document.id == dup_id).first()
                    if doc:
                        session.delete(doc)
                        deleted_count += 1
                        processed_ids.add(dup_id)
            
            # Delete duplicates by content_hash
            for dup_id in by_hash_dups:
                if dup_id not in processed_ids:
                    doc = session.query(Document).filter(Document.id == dup_id).first()
                    if doc:
                        session.delete(doc)
                        deleted_count += 1
                        processed_ids.add(dup_id)
            
            # Commit after processing each group
            if by_id_dups or by_hash_dups:
                session.commit()
    
    return {
        "duplicates_found": len(duplicates),
        "deleted": deleted_count,
        "by_id_count": by_id_count,
        "by_hash_count": by_hash_count,
    }


