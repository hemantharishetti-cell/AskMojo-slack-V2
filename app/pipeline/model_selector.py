"""
Dynamic model selection (6-factor algorithm).

Encapsulates the logic that was scattered across routes.py
lines 2465-2593.  Pure function — no I/O.
"""

from __future__ import annotations

from typing import Any

from app.prompts.constants import DEFAULT_MODEL_MINI, DEFAULT_MODEL_FULL
from app.schemas.retrieval import DataQualityAssessment
from app.utils.logging import get_logger

logger = get_logger("askmojo.pipeline.model_selector")


class ModelSelection:
    """Result of the model selection algorithm."""

    def __init__(
        self,
        model: str,
        score: int,
        breakdown: dict[str, tuple[int, str]],
        max_tokens: int,
        temperature: float,
    ):
        self.model = model
        self.score = score
        self.breakdown = breakdown
        self.max_tokens = max_tokens
        self.temperature = temperature


def select_model(
    *,
    answer_mode: str,
    data_quality: DataQualityAssessment,
    num_documents: int,
    has_complex_question: bool,
    query_length: int,
    is_follow_up: bool,
    is_clarification: bool,
    conversation_length: int,
    model_preference: str | None = None,
    max_tokens_override: int | None = None,
) -> ModelSelection:
    """
    6-factor scoring to decide between gpt-4o-mini and gpt-4o,
    plus dynamic max_tokens and temperature calculation.
    """
    score = 0
    breakdown: dict[str, tuple[int, str]] = {}

    # ── Heuristic response parameters ───────────────────────────────
    response_length, response_depth, estimated_tokens = _infer_response_params(
        answer_mode, data_quality, num_documents,
    )

    length_priority = {"comprehensive": 4, "detailed": 3, "medium": 2, "brief": 1}
    depth_priority = {"exhaustive": 4, "deep": 3, "moderate": 2, "high-level": 1}

    # Factor 1: Response length
    f1 = 2 if length_priority.get(response_length, 2) >= 4 else (
        1 if length_priority.get(response_length, 2) >= 3 else 0
    )
    score += f1
    breakdown["response_length"] = (f1, response_length)

    # Factor 2: Response depth
    f2 = 2 if depth_priority.get(response_depth, 2) >= 4 else (
        1 if depth_priority.get(response_depth, 2) >= 3 else 0
    )
    score += f2
    breakdown["response_depth"] = (f2, response_depth)

    # Factor 3: Token requirements
    f3 = 2 if estimated_tokens > 6000 else (1 if estimated_tokens > 3000 else 0)
    score += f3
    breakdown["token_requirements"] = (f3, f"{estimated_tokens} tokens")

    # Factor 4: Query complexity
    f4 = 1 if has_complex_question and query_length > 100 else 0
    score += f4
    breakdown["query_complexity"] = (f4, "complex" if f4 else "simple")

    # Factor 5: Conversation context
    f5 = 1 if is_follow_up and conversation_length > 2 else 0
    score += f5
    breakdown["conversation_context"] = (
        f5, f"{conversation_length} messages" if f5 else "first query",
    )

    # Factor 6: Data quality
    dq = data_quality.quality
    f6 = 2 if dq == "insufficient" else (1 if dq in ("low", "very_low") else 0)
    score += f6
    breakdown["data_quality"] = (f6, dq)

    # Bonus: Document count
    bonus = 1 if num_documents > 5 else 0
    score += bonus
    breakdown["document_count"] = (
        bonus, f"{num_documents} docs" if bonus else "single/few docs",
    )

    # ── Model selection ─────────────────────────────────────────────
    # Always use gpt-4o-mini to reduce cost/quota; use gpt-4o only if explicitly requested via model_preference
    if model_preference:
        model = model_preference
    else:
        model = DEFAULT_MODEL_MINI

    # ── Dynamic max tokens ──────────────────────────────────────────
    max_tokens = _calculate_max_tokens(
        estimated_tokens, model, is_follow_up, response_length,
        num_documents, dq, max_tokens_override,
    )

    # ── Temperature ─────────────────────────────────────────────────
    if has_complex_question or is_clarification:
        temperature = 0.5
    elif is_follow_up:
        temperature = 0.6
    else:
        temperature = 0.7

    logger.info(
        "Model selected: %s (score=%d, max_tokens=%d, temp=%.1f)",
        model, score, max_tokens, temperature,
    )

    return ModelSelection(
        model=model,
        score=score,
        breakdown=breakdown,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _infer_response_params(
    answer_mode: str,
    data_quality: DataQualityAssessment,
    num_documents: int,
) -> tuple[str, str, int]:
    """Infer response length, depth, and estimated tokens from heuristics."""
    # Defaults by mode
    params = {
        "extract": ("brief", "moderate", 500),
        "brief": ("brief", "moderate", 1000),
        "summarize": ("medium", "moderate", 2000),
        "explain": ("detailed", "deep", 4000),
    }
    length, depth, tokens = params.get(answer_mode, ("medium", "moderate", 3000))

    # Adjust for rich data
    if data_quality.quality == "excellent" and num_documents > 3:
        if answer_mode == "explain":
            length = "comprehensive"
            depth = "exhaustive"
            tokens = 7000

    # Adjust for insufficient data
    if data_quality.quality == "insufficient":
        if length in ("comprehensive", "detailed"):
            length = "medium"
        if depth in ("exhaustive", "deep"):
            depth = "moderate"
        tokens = min(tokens, 2000)

    tokens = max(500, min(10000, tokens))
    return length, depth, tokens


def _calculate_max_tokens(
    estimated: int,
    model: str,
    is_follow_up: bool,
    response_length: str,
    num_documents: int,
    data_quality: str,
    override: int | None,
) -> int:
    """Calculate dynamic max tokens with bounds."""
    base = estimated

    # Model headroom
    if model == DEFAULT_MODEL_FULL:
        base = int(base * 1.2)
    else:
        base = int(base * 1.1)

    # Follow-up context
    if is_follow_up:
        base = int(base * 1.15)

    # Response length
    multipliers = {"comprehensive": 1.3, "detailed": 1.2, "brief": 0.8}
    base = int(base * multipliers.get(response_length, 1.0))

    # Multiple documents
    if num_documents > 3:
        base = int(base * 1.1)

    # Insufficient data
    if data_quality == "insufficient":
        base = int(base * 0.7)

    # Override
    if override:
        base = override

    return max(500, min(16000, base))
