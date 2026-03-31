"""
Schema for Stage 1 (Query Understanding) output.

IntentDecision is the single structured object that flows from
Stage 1 into Stage 2.  Every field is explicit so downstream code
never has to guess via `if 'var' in locals()`.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Intent enum (mirrors intent_router.py) ──────────────────────────
class QuestionIntent(str, Enum):
    COUNT = "count"
    CLASSIFICATION = "classification"
    EXISTENCE = "existence"
    DOCUMENT_LISTING = "document_listing"
    DOMAIN_QUERY = "domain_query"
    FACTUAL_CONTENT = "factual_content"
    HYBRID = "hybrid"
    CONVERSATIONAL = "conversational"


# ── Attribute enum (hard routing constraint) ─────────────────────────
class QuestionAttribute(str, Enum):
    """
    Attribute is a HARD ROUTING CONSTRAINT.
    Determines which subsystem(s) can answer the question.
    """
    METADATA_ONLY = "metadata_only"
    DOCUMENT_EXIST = "document_exist"
    DOCUMENT_COUNT = "document_count"
    DOCUMENT_CATEGORY = "document_category"
    DOCUMENT_REFERENCE = "document_reference"
    DOCUMENT_LISTING = "document_listing"
    DOMAIN_QUERY = "domain_query"
    FACTUAL = "factual"


# ── Stage 1 output schema ───────────────────────────────────────────
class IntentDecision(BaseModel):
    """
    Complete decision object produced by Stage 1 (Query Understanding).
    Gates all downstream behavior—retrieval, prompt mode, etc.
    """
    intent: QuestionIntent
    attribute: QuestionAttribute
    refined_question: str
    selected_collections: list[str] = Field(default_factory=list)
    answer_mode: str = "explain"  # extract | brief | summarize | explain
    entity: str | None = None
    sales_intent: str | None = None
    buying_stage: str | None = None
    core_fear: str | None = None

    # Routing control
    proceed_to_retrieval: bool = True
    short_circuit_answer: str | None = None  # If metadata-only, answer is here

    # Conversation context
    is_follow_up: bool = False
    is_clarification: bool = False
    conversation_length: int = 0

    # Original hints from the rule-based classifier
    intent_hints: dict[str, Any] = Field(default_factory=dict)

    # Option C: Solution-selection layer output (single best solution + rationale)
    selected_solution: str | None = None
    solution_rationale: str | None = None

    class Config:
        use_enum_values = True
