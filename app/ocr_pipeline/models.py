"""
Pydantic models for the OCR pipeline structured output.

These models define the JSON schema that OpenAI returns after structuring
the raw OCR text into document chunks.
"""

from typing import List
from pydantic import BaseModel, ConfigDict


class DocumentMetadata(BaseModel):
    """Global metadata extracted once per document."""
    model_config = ConfigDict(extra="allow") # Allow extra for flexibility
    document_title: str
    total_pages: int
    processed_pages: int
    doc_type: str = "other"
    domain: str = ""
    solution_area: str = ""
    industry: str = ""
    technology: str = ""
    use_case: str = ""
    entity: str = "internal"
    created_at: int = 0


class ChunkMetadata(BaseModel):
    """Structural metadata extracted per chunk."""
    model_config = ConfigDict(extra="allow")
    page_number: int
    chunk_index: int
    content_type: str
    confidence: float
    # Derived or enriched fields
    section_path: str = ""
    is_table: int = 0


class DocumentChunk(BaseModel):
    model_config = ConfigDict(extra="allow")
    chunk_id: str
    h1: str = ""
    h2: str = ""
    content: str
    keywords: List[str] = []
    metadata: ChunkMetadata


class StructuredDocument(BaseModel):
    model_config = ConfigDict(extra="allow")
    metadata: DocumentMetadata
    chunks: List[DocumentChunk]
    document_description: dict | None = None
