"""
Pipeline Stage 1a: Rule-based intent classification.

Wraps the existing intent_router.py functions and enriches the result
into an IntentDecision schema object. Zero cost, < 1 ms.
"""

from __future__ import annotations

from typing import Any

from app.schemas.intent import IntentDecision, QuestionIntent, QuestionAttribute
from app.utils.text import extract_entity, infer_core_fear, infer_answer_mode
from app.utils.logging import get_logger

# Re-export from existing module so nothing breaks
from app.vector_logic.intent_router import (
    classify_intent as _classify_intent,
    map_intent_to_attribute as _map_intent_to_attribute,
    recommend_solution,
    SOLUTION_KEYWORDS,
    handle_objection,
)

logger = get_logger("askmojo.pipeline.intent")


def classify_intent(question: str) -> tuple[QuestionIntent, dict[str, Any]]:
    """
    Rule-based intent classification — direct proxy to intent_router.py.

    Returns (QuestionIntent enum, hints dict).
    """
    return _classify_intent(question)


def map_intent_to_attribute(intent: QuestionIntent) -> QuestionAttribute:
    """Map Intent → Attribute for hard routing constraints."""
    return _map_intent_to_attribute(intent)


def build_intent_decision(
    question: str,
    *,
    conversation_history: list[dict] | None = None,
) -> IntentDecision:
    """
    Run the full rule-based Stage 1a classification and produce
    an IntentDecision with all fields populated.

    This function is pure except for logging.
    """
    intent, hints = classify_intent(question)
    attribute = map_intent_to_attribute(intent)
    entity = extract_entity(question)
    core_fear = infer_core_fear(question)
    answer_mode = infer_answer_mode(question)

    conv = conversation_history or []
    is_follow_up = len(conv) > 0
    is_clarification = any(
        w in question.lower()
        for w in [
            "what do you mean", "can you explain", "clarify",
            "elaborate", "more details", "again",
        ]
    )

    decision = IntentDecision(
        intent=intent,
        attribute=attribute,
        refined_question=question,  # Will be overridden by LLM in Stage 1b
        entity=entity,
        core_fear=core_fear,
        answer_mode=answer_mode,
        sales_intent=hints.get("sales_intent"),
        buying_stage=hints.get("buying_stage"),
        intent_hints=hints,
        is_follow_up=is_follow_up,
        is_clarification=is_clarification,
        conversation_length=len(conv),
    )

    logger.info(
        "[INTENT] Classified: intent=%s | attribute=%s | entity=%s",
        decision.intent, decision.attribute, decision.entity or "—",
    )

    return decision
