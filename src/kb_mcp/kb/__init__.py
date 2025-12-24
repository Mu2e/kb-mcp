"""Knowledge base module for document storage and retrieval."""

from .documents import add_document, add_source, get, get_count, delete_document
from .db_models import Document, Source
from .database import get_db_session, get_db_session_ephemeral, init_db
from .utils import list_sources, get_metadata_keys
from .tools import ingest, parse_all, chunk_and_embed_all, image_chunk_and_embed_all, embed_all

# Internal imports (not exported in __all__ but available for internal use)
from .documents import add_parsed, add_parsed_many, get_or_create_parser, get_or_create_raw_document, insert_raw_document, get_options, get_children  # noqa: F401
from .utils import deduplicate, find_all_duplicates, get_stats  # noqa: F401
from .logs import get_search_logs, get_parsing_logs, get_chunking_logs, get_all_logs_for_document  # noqa: F401

# Import statistics and embedding models
from .statistics import get_statistics
from .embedding.db_models import Chunk, EmbeddingConfig

__all__ = [
    # High-level ingestion
    "ingest",
    "add_document",

    # Document operations
    "get",
    "get_count",
    "delete_document",

    # Source management
    "add_source",
    "list_sources",

    # Metadata utilities
    "get_metadata_keys",

    # Batch operations
    "parse_all",
    "chunk_and_embed_all",
    "image_chunk_and_embed_all",
    "embed_all",

    # Models
    "Document",
    "Source",
    "Chunk",
    "EmbeddingConfig",

    # Statistics
    "get_statistics",

    # Database
    "get_db_session",
    "get_db_session_ephemeral",
    "init_db",

    # Search
    "search",
]

from .search import search


