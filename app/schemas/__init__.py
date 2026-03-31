"""
Pydantic schemas for every pipeline boundary.
Each module covers one pipeline stage or cross-cutting concern.
"""

from app.schemas.intent import (
    IntentDecision,
    QuestionIntent,
    QuestionAttribute,
)
from app.schemas.retrieval import (
    RetrievalResult,
    DocumentResult,
    ChunkResult,
    DataQualityAssessment,
)
from app.schemas.response import (
    FinalResponse,
    PipelineMetadata,
    AskRequest,
    AskResponse,
    SourceChunk,
    TokenUsage,
    APICallResponse,
)
from app.schemas.quality import QualityScore
from app.schemas.pipeline import PipelineContext

__all__ = [
    # Intent
    "IntentDecision",
    "QuestionIntent",
    "QuestionAttribute",
    # Retrieval
    "RetrievalResult",
    "DocumentResult",
    "ChunkResult",
    "DataQualityAssessment",
    # Response
    "FinalResponse",
    "PipelineMetadata",
    "AskRequest",
    "AskResponse",
    "SourceChunk",
    "TokenUsage",
    "APICallResponse",
    # Quality
    "QualityScore",
    # Pipeline
    "PipelineContext",
]
