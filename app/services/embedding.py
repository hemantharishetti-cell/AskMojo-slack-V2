"""
Embedding model singleton and batch embedding.

Re-exports the existing helpers from vector_store and adds a thin
wrapper for batch usage in the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from app.vector_logic.vector_store import (
    _get_embedding_model,
    _embed_query,
    embed_worker,
)

logger = logging.getLogger("askmojo.services.embedding")

__all__ = [
    "get_embedding_model",
    "embed_query",
    "embed_worker",
    "embed_batch",
]


def get_embedding_model() -> Any:
    """Return the cached SentenceTransformer model."""
    return _get_embedding_model()


def embed_query(text: str) -> list[float]:
    """Generate an embedding vector for a single query string."""
    return _embed_query(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of texts using the cached model.
    Runs synchronously on the caller's thread (use ProcessPoolExecutor
    for CPU-parallel workloads).
    """
    model = get_embedding_model()
    return model.encode(texts, show_progress_bar=False).tolist()
