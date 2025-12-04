"""Tools for batch operations on the knowledge base."""

import logging
from typing import Dict, Any, Optional

from .database import get_db_session
from .core import Document
from .embedding.core import Chunk

logger = logging.getLogger(__name__)

# Import embedding functions (may not be available)
try:
    from .embedding import chunk_and_embed
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    chunk_and_embed = None


def chunk_and_embed_all(
    source_id: str,
    strategy: Optional[str] = None,
) -> Dict[str, Any]:
    """Chunk and embed all documents for a source_id that don't have chunks yet.
    
    Uses SQL JOINs to efficiently find documents without chunks, then chunks and embeds them.
    Assumes that if chunks exist, they are already embedded.
    
    Args:
        source_id: Source identifier to process documents for
        strategy: Optional chunking strategy ("tokens" or "slide"). If None, uses default.
    
    Returns:
        Dictionary with:
        - processed: Number of documents processed
        - chunked: Number of documents that were chunked and embedded
        - skipped: Number of documents skipped (no text or already had chunks)
        - errors: Number of documents that failed
    
    Example:
        >>> from test_mcp.kb.tools import chunk_and_embed_all
        >>> result = chunk_and_embed_all("inspire-hep")
        >>> print(f"Processed {result['processed']} documents, chunked {result['chunked']}")
    """
    if not EMBEDDING_AVAILABLE:
        raise ImportError(
            "Embedding module not available. Install with: pip install -e '.[embedding]'"
        )
    
    logger.info(f"Starting chunk_and_embed_all for source_id: {source_id}")
    
    with get_db_session() as session:
        # Find documents without chunks using LEFT JOIN
        query = session.query(Document).filter(
            Document.source_id == source_id,
            Document.text.isnot(None),
            Document.text != ""
        ).outerjoin(Chunk, Document.id == Chunk.document_id).filter(Chunk.id.is_(None))
        
        documents = query.all()
        
        if not documents:
            logger.info(f"No documents found for source_id: {source_id} that need chunking")
            return {
                "processed": 0,
                "chunked": 0,
                "skipped": 0,
                "errors": 0,
            }
        
        logger.info(f"Found {len(documents)} document(s) for source_id: {source_id} that need chunking")
        
        processed = 0
        chunked = 0
        skipped = 0
        errors = 0
        
        for doc in documents:
            try:
                processed += 1
                
                # Chunk and embed the document
                logger.info(f"Chunking and embedding document {doc.id} ({doc.doc_id or doc.id})")
                chunks = chunk_and_embed(
                    doc,
                    strategy=strategy,
                    session=session,
                )
                
                if chunks:
                    chunked += 1
                    logger.info(f"Successfully chunked and embedded document {doc.id} ({len(chunks)} chunks)")
                else:
                    skipped += 1
                    logger.warning(f"No chunks created for document {doc.id}")
                    
            except Exception as e:
                errors += 1
                logger.error(f"Error processing document {doc.id}: {e}", exc_info=True)
                continue
    
    result = {
        "processed": processed,
        "chunked": chunked,
        "skipped": skipped,
        "errors": errors,
    }
    
    logger.info(
        f"Completed chunk_and_embed_all for source_id: {source_id}. "
        f"Processed: {processed}, Chunked: {chunked}, Skipped: {skipped}, Errors: {errors}"
    )
    
    return result

