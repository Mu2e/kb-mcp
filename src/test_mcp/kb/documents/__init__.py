"""
Document operations module.

This module provides core CRUD operations for documents in the knowledge base.
"""

from .operations import (
    add,
    add_many,
    add_from_path,
    get,
    get_count,
    get_children,
    add_source,
    delete_document,
    get_options,
)

__all__ = [
    "add",
    "add_many",
    "add_from_path",
    "get",
    "get_count",
    "get_children",
    "add_source",
    "delete_document",
    "get_options",
]
