"""
Pipeline Orchestrator — top-level entry point.

Calls Stage 1 → Stage 2 → Stage 3 in sequence, with short-circuit
exits where appropriate.  Each stage is independently callable.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.intent import IntentDecision, QuestionAttribute
from app.schemas.pipeline import PipelineContext
from app.schemas.response import FinalResponse, AskResponse, PipelineMetadata
from app.schemas.retrieval import RetrievalResult
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger("askmojo.pipeline.orchestrator")


async def run_pipeline(
    question: str,
    db: Session,
    *,
    slack_user_email: str | None = None,
    conversation_history: list[dict] | None = None,
    max_tokens: int | None = None,
    model_preference: str | None = None,
) -> FinalResponse:
    """
    Execute the full 3-stage RAG pipeline.

    This is the single entry-point that replaces the 2400-line
    `ask_question()` function in routes.py.

    Stages:
      1. Query Understanding (intent classification + LLM rewrite)
      2. Retrieval (master search + parallel chunk retrieval)
      3. Response Synthesis (prompt building + LLM generation + rubric)
    """
    ctx = PipelineContext(
        raw_question=question,
        slack_user_email=slack_user_email,
        conversation_history=conversation_history or [],
        max_tokens_override=max_tokens,
        model_preference=model_preference,
    )

    logger.info("[PIPELINE] Started | question: %s", question[:80])

    # ── Stage 1: Query Understanding ────────────────────────────────
    with Timer("stage_1_query_understanding") as t1:
        ctx = await _run_stage_1(ctx, db)
    ctx.stage_timings["stage_1"] = t1.elapsed_s
    intent_str = ctx.intent_decision.intent if ctx.intent_decision else "—"
    logger.info("[PIPELINE] Stage 1 done (%.2fs) | intent=%s", t1.elapsed_s, intent_str)

    # Short-circuit if metadata answered the query
    if ctx.final_response is not None:
        ctx.final_response.processing_time_seconds = ctx.elapsed_seconds
        logger.info("[PIPELINE] Short-circuit: metadata answer (no retrieval)")
        return ctx.final_response

    # Short-circuit if LLM rewrite said not to proceed
    if ctx.intent_decision and not ctx.intent_decision.proceed_to_retrieval:
        answer = (
            ctx.intent_decision.short_circuit_answer
            or _fallback_non_proceed_answer(question, db)
        )
        logger.info("[PIPELINE] Short-circuit: no retrieval (proceed_to_retrieval=False)")
        return FinalResponse(
            answer=answer,
            processing_time_seconds=ctx.elapsed_seconds,
            pipeline_metadata=_build_metadata(ctx),
        )

    # ── Stage 2: Retrieval ──────────────────────────────────────────
    with Timer("stage_2_retrieval") as t2:
        ctx = await _run_stage_2(ctx, db)
    ctx.stage_timings["stage_2"] = t2.elapsed_s
    docs = len(ctx.retrieval_result.documents) if ctx.retrieval_result else 0
    chunks = len(ctx.retrieval_result.chunks) if ctx.retrieval_result else 0
    logger.info("[PIPELINE] Stage 2 done (%.2fs) | documents=%s, chunks=%s", t2.elapsed_s, docs, chunks)

    # ── Stage 3: Response Synthesis ─────────────────────────────────
    with Timer("stage_3_synthesis") as t3:
        ctx = await _run_stage_3(ctx)
    ctx.stage_timings["stage_3"] = t3.elapsed_s
    logger.info("[PIPELINE] Stage 3 done (%.2fs)", t3.elapsed_s)

    if ctx.final_response is not None:
        ctx.final_response.processing_time_seconds = ctx.elapsed_seconds
        return ctx.final_response

    # Fallback (should not reach here)
    return FinalResponse(
        answer="I was unable to generate a response. Please try rephrasing your question.",
        processing_time_seconds=ctx.elapsed_seconds,
    )


# ── Stage runners ───────────────────────────────────────────────────

async def _run_stage_1(ctx: PipelineContext, db: Session) -> PipelineContext:
    """
    Stage 1: Query Understanding.

    1a. Rule-based intent classification
    1b. Metadata short-circuit (if applicable)
    1c. LLM-based query rewriting + collection selection (if FACTUAL)
    """
    from app.pipeline.intent import build_intent_decision
    from app.pipeline.metadata_handler import try_metadata_short_circuit
    from app.pipeline.query_rewrite import rewrite_and_select
    from app.prompts.constants import select_role, select_response_type
    from app.sqlite.models import Category

    # 1a. Rule-based classification
    intent_decision = build_intent_decision(
        ctx.raw_question,
        conversation_history=ctx.conversation_history,
    )
    ctx.intent_decision = intent_decision

    # 1b. Try metadata short-circuit
    categories = db.query(Category).filter(Category.is_active == True).all()
    meta_response = try_metadata_short_circuit(
        intent_decision, ctx.raw_question, db, categories,
    )
    if meta_response is not None:
        ctx.final_response = meta_response
        return ctx

    # Defensive gate: only FACTUAL proceeds to RAG
    if intent_decision.attribute != QuestionAttribute.FACTUAL:
        logger.warning(
            "Defensive gate: attribute=%s is not FACTUAL, aborting RAG",
            intent_decision.attribute,
        )
        ctx.final_response = FinalResponse(
            answer=(
                "This request was routed to a non-factual handler. "
                "Please rephrase your question."
            ),
        )
        return ctx

    # 1c. LLM rewrite + collection selection
    categories_data = [
        {
            "collection_name": cat.collection_name,
            "category_name": cat.name,
            "domains": [d.name for d in cat.domains] if cat.domains else [],
            "description": cat.description or "No description available",
        }
        for cat in categories
    ]

    intent_decision = await rewrite_and_select(
        intent_decision,
        categories_data,
        conversation_history=ctx.conversation_history,
    )
    ctx.intent_decision = intent_decision

    # Option C: Solution-selection layer (pick single best solution for recommendation-style questions)
    # Commented out temporarily to reduce LLM calls and avoid 429 rate limit / quota errors
    # from app.pipeline.solution_selector import select_solution
    # selected_sol, rationale = await select_solution(
    #     question=intent_decision.refined_question,
    #     selected_collections=intent_decision.selected_collections,
    #     answer_mode=intent_decision.answer_mode,
    # )
    # if selected_sol:
    #     intent_decision.selected_solution = selected_sol
    #     intent_decision.solution_rationale = rationale
    #     logger.info("[PIPELINE] Solution selected: %s", selected_sol)

    # Set role and response type
    ctx.role = select_role(intent_decision.intent, intent_decision.intent_hints)
    ctx.response_type = select_response_type(
        intent_decision.intent, ctx.role,
        ctx.raw_question, intent_decision.intent_hints,
    )

    return ctx


async def _run_stage_2(ctx: PipelineContext, db: Session) -> PipelineContext:
    """
    Stage 2: Retrieval.

    1. Search master_docs collection
    2. Filter documents
    3. Retrieve chunks in parallel per collection
    4. Score and prune chunks (token budget)
    """
    from app.pipeline.retrieval import retrieve_documents_and_chunks

    if ctx.intent_decision is None:
        logger.error("Stage 2 called without intent_decision")
        return ctx

    retrieval_result = await retrieve_documents_and_chunks(
        intent_decision=ctx.intent_decision,
        db=db,
    )
    ctx.retrieval_result = retrieval_result
    return ctx


async def _run_stage_3(ctx: PipelineContext) -> PipelineContext:
    """
    Stage 3: Response Synthesis.

    1. Select model
    2. Build prompt
    3. Generate answer (LLM call)
    4. Evaluate quality (rubric)
    5. Optional: Refinement pass
    6. Format for output
    """
    from app.pipeline.response_generator import generate_response

    if ctx.intent_decision is None or ctx.retrieval_result is None:
        logger.error("Stage 3 called without required context")
        return ctx

    final_response = await generate_response(
        ctx=ctx,
    )
    ctx.final_response = final_response
    return ctx


# ── Helpers ─────────────────────────────────────────────────────────

def _fallback_non_proceed_answer(question: str, db: Session) -> str:
    """Generate a fallback answer when LLM says not to proceed."""
    from app.sqlite.models import Category

    q = question.lower().strip()

    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}
    if q in greetings:
        return (
            "Hello! I'm ASKMOJO, your AI assistant. "
            "I can help you find information from your documents. "
            "What would you like to know?"
        )

    thanks = {"thanks", "thank you"}
    if q in thanks:
        return "You're welcome! Feel free to ask if you need anything else."

    bye = {"bye", "goodbye", "see you"}
    if q in bye:
        return "Goodbye! Feel free to come back if you need any help."

    categories = db.query(Category).filter(Category.is_active == True).all()
    names = [c.name for c in categories] if categories else []
    topics = ", ".join(names) if names else "topics covered in your documents"
    return (
        f"I can only answer questions related to the documents in my knowledge base. "
        f"Based on your available collections, I can help with: {topics}. "
        f"How can I assist you?"
    )


def _build_metadata(ctx: PipelineContext) -> PipelineMetadata:
    """Build pipeline metadata from current context."""
    d = ctx.intent_decision
    if d is None:
        return PipelineMetadata()
    return PipelineMetadata(
        intent=d.intent,
        attribute=d.attribute,
        answer_mode=d.answer_mode,
        model_used=ctx.selected_model,
        collections_searched=d.selected_collections,
        selected_solution=getattr(d, "selected_solution", None),
    )


def pipeline_response_to_ask_response(response: FinalResponse) -> AskResponse:
    """
    Convert the internal FinalResponse to the external AskResponse
    for backwards compatibility.
    """
    return AskResponse(
        answer=response.answer,
        token_usage=response.token_usage,
        toon_savings=response.toon_savings,
        followups=response.followups,
        sources=response.sources,
    )
