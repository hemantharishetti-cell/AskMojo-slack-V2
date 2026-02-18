"""
Schemas for Stage 3 (Response Synthesis) output and API layer.

FinalResponse is the pipeline-internal result.
AskResponse is the external API contract (backwards-compatible).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.quality import QualityScore


# ── Pipeline-internal response ──────────────────────────────────────
class PipelineMetadata(BaseModel):
    """Diagnostic metadata attached to every response."""
    intent: str = ""
    attribute: str = ""
    answer_mode: str = ""
    model_used: str = ""
    collections_searched: list[str] = Field(default_factory=list)
    documents_found: int = 0
    chunks_retrieved: int = 0
    data_quality: str = "sufficient"
    confidence_score: int = 50
    selected_solution: str | None = None  # Option C: single solution chosen by solution selector


class FinalResponse(BaseModel):
    """
    Complete pipeline output.  Guaranteed shape regardless of which
    code path (metadata short-circuit, full RAG, etc.) produced it.
    """
    answer: str
    sources: list[str] | None = None
    followups: list[dict] | None = None
    token_usage: dict | None = None
    toon_savings: dict | None = None
    processing_time_seconds: float = 0.0
    pipeline_metadata: PipelineMetadata | None = None
    quality_score: QualityScore | None = None


# ── External API schemas (backwards-compatible) ─────────────────────
class AskRequest(BaseModel):
    question: str
    slack_user_email: str | None = None
    conversation_history: list[dict] | None = None
    max_tokens: int | None = None
    model_preference: str | None = None


class SourceChunk(BaseModel):
    document_id: int
    document_title: str
    category: str | None = None
    chunk_text: str
    page_number: int | None = None
    chunk_index: int | None = None
    score: float = 0.0


class TokenUsage(BaseModel):
    """Token usage information for a single API call."""
    call_name: str
    json_tokens: int
    toon_tokens: int
    savings: int
    savings_percent: float


class APICallResponse(BaseModel):
    """Response data for a single API call."""
    call_name: str
    request_prompt: str | None = None
    response_content: str | dict | None = None
    model_used: str | None = None
    tokens_used: int | None = None
    tokens_without_toon: int | None = None
    savings: int | None = None
    savings_percent: float | None = None


class AskResponse(BaseModel):
    """
    Public API response — backwards-compatible with existing Slack
    integration and any other consumers.
    """
    answer: str
    token_usage: dict | None = None
    toon_savings: dict | None = None
    api_calls: list[APICallResponse] | None = None
    followups: list[dict] | None = None
    sources: list[str] | None = None
