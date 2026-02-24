"""
Pipeline Stage 1: Metadata-only handlers.

Consolidates all metadata query handling from intent_router.py and
the duplicate logic in routes.py lines 1688-1833.  These handlers
answer directly from the database — NO RAG, NO embedding queries.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.schemas.intent import IntentDecision, QuestionAttribute
from app.schemas.response import FinalResponse, PipelineMetadata
from app.utils.text import extract_entity
from app.utils.logging import get_logger

# Re-export existing handlers
from app.vector_logic.intent_router import (
    handle_count,
    handle_classification,
    handle_existence,
    handle_conversational,
    handle_listing,
    handle_domain_query,
    handle_objection,
)

logger = get_logger("askmojo.pipeline.metadata_handler")


def try_metadata_short_circuit(
    intent_decision: IntentDecision,
    question: str,
    db: Session,
    categories: list,
) -> FinalResponse | None:
    """
    Attempt to answer the query purely from the database.

    Returns a FinalResponse if the question can be fully answered
    from metadata, or None if the pipeline should continue to
    retrieval (Stage 2).

    This function consolidates ALL the hard-stop checks from
    routes.py lines 1134-1190.
    """
    attr = intent_decision.attribute
    entity = intent_decision.entity
    hints = intent_decision.intent_hints

    answer: str | None = None

    # ── Sales objection short-circuit ────────────────────────────────
    if hints.get("sales_intent") == "Objection":
        ob_resp = handle_objection(question)
        if ob_resp:
            logger.info("Sales objection handled via template")
            return _build_meta_response(ob_resp, intent_decision)

    # ── METADATA_ONLY → conversational ──────────────────────────────
    if attr == QuestionAttribute.METADATA_ONLY:
        answer = handle_conversational(question)
        logger.info("HARD STOP: METADATA_ONLY → CONVERSATIONAL")

    # ── DOCUMENT_COUNT ──────────────────────────────────────────────
    elif attr == QuestionAttribute.DOCUMENT_COUNT:
        logger.info("metadata_short_circuit_triggered: DOCUMENT_COUNT")
        answer = handle_count(question, db, categories, hints)
        logger.info("HARD STOP: DOCUMENT_COUNT")
        return _build_meta_response(answer, intent_decision)

    # ── DOCUMENT_EXIST ──────────────────────────────────────────────
    elif attr == QuestionAttribute.DOCUMENT_EXIST:
        answer = handle_existence(question, db, categories, entity, hints)
        logger.info("HARD STOP: DOCUMENT_EXIST")

    # ── DOCUMENT_CATEGORY ───────────────────────────────────────────
    elif attr == QuestionAttribute.DOCUMENT_CATEGORY:
        answer = handle_classification(question, db, categories, entity)
        if answer is None:
            answer = (
                "I couldn't determine the category/domain classification "
                "from the registry. Could you rephrase your question?"
            )
        logger.info("HARD STOP: DOCUMENT_CATEGORY")

    # ── DOCUMENT_LISTING ────────────────────────────────────────────
    elif attr == QuestionAttribute.DOCUMENT_LISTING:
        logger.info("metadata_short_circuit_triggered: DOCUMENT_LISTING")
        answer = handle_listing(question, db, categories, hints)
        logger.info("HARD STOP: DOCUMENT_LISTING")
        return _build_meta_response(answer, intent_decision)

    # ── DOMAIN_QUERY ────────────────────────────────────────────────
    elif attr == QuestionAttribute.DOMAIN_QUERY:
        logger.info("metadata_short_circuit_triggered: DOMAIN_QUERY")
        answer = handle_domain_query(question, db, categories, hints)
        logger.info("HARD STOP: DOMAIN_QUERY")
        return _build_meta_response(answer, intent_decision)

    # ── DOCUMENT_REFERENCE → registry-first ─────────────────────────
    elif attr == QuestionAttribute.DOCUMENT_REFERENCE:
        answer = handle_existence(question, db, categories, entity, hints)
        logger.info("HARD STOP: DOCUMENT_REFERENCE (registry-first)")

    # ── FACTUAL → continue to retrieval ─────────────────────────────
    if answer is None:
        return None  # Signal: proceed to Stage 2

    return _build_meta_response(answer, intent_decision)


def _build_meta_response(
    answer: str,
    intent_decision: IntentDecision,
) -> FinalResponse:
    """Wrap a metadata-only answer into a FinalResponse."""
    return FinalResponse(
        answer=answer,
        pipeline_metadata=PipelineMetadata(
            intent=intent_decision.intent,
            attribute=intent_decision.attribute,
            answer_mode=intent_decision.answer_mode,
            model_used="none (metadata-only)",
        ),
    )
