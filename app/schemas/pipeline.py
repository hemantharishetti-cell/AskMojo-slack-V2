"""
PipelineContext carries mutable state between the 3 pipeline stages.

This replaces the 15+ local variables that used to be checked via
`if 'var' in locals()` inside the monolithic ask_question().
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.intent import IntentDecision
from app.schemas.retrieval import RetrievalResult
from app.schemas.response import FinalResponse


class PipelineContext(BaseModel):
    """
    Shared context object threaded through all 3 pipeline stages.

    Created once at the start of the pipeline run and progressively
    enriched by each stage.
    """

    # ── Inputs ───────────────────────────────────────────────────────
    raw_question: str
    slack_user_email: str | None = None
    conversation_history: list[dict] = Field(default_factory=list)
    max_tokens_override: int | None = None
    model_preference: str | None = None

    # ── Stage outputs (populated progressively) ─────────────────────
    intent_decision: IntentDecision | None = None
    retrieval_result: RetrievalResult | None = None
    final_response: FinalResponse | None = None

    # ── Cross-cutting state ─────────────────────────────────────────
    role: str = "Sales"
    response_type: str = "SALES_RECOMMENDATION"
    selected_model: str = "gpt-4o-mini"
    dynamic_max_tokens: int = 2000
    temperature: float = 0.4

    # ── Timing ──────────────────────────────────────────────────────
    start_time: float = Field(default_factory=time.time)
    stage_timings: dict[str, float] = Field(default_factory=dict)

    # ── Token tracking ──────────────────────────────────────────────
    token_usage_tracker: dict[str, Any] = Field(default_factory=lambda: {
        "calls": [],
        "total_json_tokens": 0,
        "total_toon_tokens": 0,
        "total_savings": 0,
        "total_savings_percent": 0.0,
    })
    api_call_responses: list[dict] = Field(default_factory=list)

    # --- Added for logging ---
    token_usage: dict | None = None
    toon_savings: dict | None = None

    class Config:
        arbitrary_types_allowed = True

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.start_time
