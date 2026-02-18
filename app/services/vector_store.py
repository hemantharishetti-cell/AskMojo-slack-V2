"""
ChromaDB operations — cleaned wrapper around the original vector_store.py.

This module re-exports the existing functions from
`app.vector_logic.vector_store` so the rest of the new pipeline can
import from a single canonical location.  When the old module is
eventually deleted, the implementations will live here.
"""

from __future__ import annotations

from app.vector_logic.vector_store import (
    init_chromadb,
    list_collections,
    query_collection,
    query_collection_with_filter,
    query_master_collection,
    store_chunks_in_chromadb,
    store_document_in_master_collection,
    delete_document_from_chromadb,
    rename_chromadb_collection,
    ensure_collection_exists,
    update_collection_metadata,
    _get_chroma_client,
    _embed_query,
)

__all__ = [
    "init_chromadb",
    "list_collections",
    "query_collection",
    "query_collection_with_filter",
    "query_master_collection",
    "store_chunks_in_chromadb",
    "store_document_in_master_collection",
    "delete_document_from_chromadb",
    "rename_chromadb_collection",
    "ensure_collection_exists",
    "update_collection_metadata",
    "_get_chroma_client",
    "_embed_query",
]
