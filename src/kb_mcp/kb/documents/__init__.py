"""
Document operations module.

This module provides core CRUD operations for documents in the knowledge base.
"""

from .operations import (
    add_parsed,
    add_parsed_many,
    add_document,
    get,
    get_count,
    get_children,
    add_source,
    delete_document,
    delete_raw_document,
    get_raw_document,
    get_options,
    get_or_create_parser,
    get_or_create_raw_document,
    insert_raw_document,
)

__all__ = [
    "add_parsed",
    "add_parsed_many",
    "add_document",
    "get",
    "get_count",
    "get_children",
    "add_source",
    "delete_document",
    "delete_raw_document",
    "get_raw_document",
    "get_options",
    "get_or_create_parser",
    "get_or_create_raw_document",
    "insert_raw_document",
]
