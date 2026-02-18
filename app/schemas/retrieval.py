"""
Schemas for Stage 2 (Retrieval) output.

RetrievalResult carries documents and chunks from Stage 2
into Stage 3.  Every chunk has a document_id and score
so downstream code never does `chunk_metadata.get("document_id")`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentResult(BaseModel):
    """One relevant document found in the master_docs collection."""
    document_id: int
    title: str
    collection_name: str
    description: str = ""
    doc_type: str = "other"
    relevance_score: float = 0.0
    top_k_chunks: int = 5


class ChunkResult(BaseModel):
    """One chunk retrieved from a category-specific ChromaDB collection."""
    document_id: int
    document_title: str
    category: str | None = None
    chunk_text: str
    page_number: int | None = None
    chunk_index: int | None = None
    score: float = 0.0


class DataQualityAssessment(BaseModel):
    """Data quality metadata produced by the chunk scorer."""
    quality: str = "sufficient"  # excellent | good | sufficient | insufficient
    confidence_score: int = 50  # 0-100
    relevance_quality: str = "medium"  # high | medium | low | very_low
    avg_chunk_distance: float = 0.0
    total_chunks: int = 0
    total_documents: int = 0


class RetrievalResult(BaseModel):
    """
    Complete output of Stage 2.
    Flows into Stage 3 (Response Synthesis).
    """
    documents: list[DocumentResult] = Field(default_factory=list)
    chunks: list[ChunkResult] = Field(default_factory=list)
    data_quality: DataQualityAssessment = Field(default_factory=DataQualityAssessment)

    # Token budget tracking
    total_chunk_tokens: int = 0
    budget_applied: bool = False

    # Summaries for prompt building (TOON-encoded)
    summaries_toon: str = ""
    chunks_toon: str = ""
