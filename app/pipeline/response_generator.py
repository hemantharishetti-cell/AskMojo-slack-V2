"""
Pipeline Stage 3: Response Synthesis.

1. Select model (6-factor algorithm)
2. Build prompt (mode-specific)
3. Generate answer (LLM call)
4. Evaluate quality (5-criterion rubric)
5. Optional: Refinement pass
6. Build follow-ups, sources, and format for output
"""

from __future__ import annotations

import re
from typing import Any

from app.schemas.intent import IntentDecision
from app.schemas.pipeline import PipelineContext
from app.schemas.quality import QualityScore
from app.schemas.response import FinalResponse, PipelineMetadata
from app.schemas.retrieval import RetrievalResult
from app.services.llm import get_openai_client
from app.prompts.answer_generator import build_system_prompt, build_answer_prompt
from app.prompts.refinement import build_refinement_instruction
from app.prompts.constants import (
    RESPONSE_TYPES,
    BANNED_PHRASES,
    build_constraints,
)
from app.pipeline.model_selector import select_model
from app.utils.text import humanize_title, infer_core_fear
from app.utils.logging import get_logger

logger = get_logger("askmojo.pipeline.response_generator")



async def generate_response(ctx: PipelineContext) -> FinalResponse:
    """
    Full Stage 3: answer generation, quality evaluation, and
    optional refinement.
    """
    # --- Comparison question detection ---
    is_comparison = any(
        w in ctx.raw_question.lower()
        for w in ["different", "difference", "compare", "instead of"]
    )
    # --- Discovery question detection ---
    is_discovery_question = "discovery" in ctx.raw_question.lower()
    # --- Proof-type question detection ---
    is_proof_question = any(
        phrase in ctx.raw_question.lower()
        for phrase in ["handled", "experience", "before", "proof", "scale"]
    )

    # --- Multi-problem mapping detection ---
    def extract_list_items(question: str) -> list[str]:
        lines = [l.strip() for l in question.split("\n") if l.strip()]
        # Skip first line if it’s instruction
        if len(lines) > 1:
            return lines[1:]
        return []

    list_items = extract_list_items(ctx.raw_question)
    is_multi_problem = len(list_items) >= 2

    intent = ctx.intent_decision
    retrieval = ctx.retrieval_result
    if intent is None or retrieval is None:
        return FinalResponse(answer="Unable to generate a response — missing context.")


    # --- Multi-problem mapping detection ---
    def extract_list_items(question: str) -> list[str]:
        lines = [l.strip() for l in question.split("\n") if l.strip()]
        # Skip first line if it’s instruction
        if len(lines) > 1:
            return lines[1:]
        return []

    list_items = extract_list_items(ctx.raw_question)
    is_multi_problem = len(list_items) >= 2

    # ── 1. Model selection ──────────────────────────────────────────
    question_words = [
        "what", "how", "why", "when", "where", "who",
        "which", "explain", "describe", "tell me",
    ]
    has_complex = any(w in ctx.raw_question.lower() for w in question_words)

    model_sel = select_model(
        answer_mode=intent.answer_mode,
        data_quality=retrieval.data_quality,
        num_documents=len(retrieval.documents),
        has_complex_question=has_complex,
        query_length=len(ctx.raw_question),
        is_follow_up=intent.is_follow_up,
        is_clarification=intent.is_clarification,
        conversation_length=intent.conversation_length,
        model_preference=ctx.model_preference,
        max_tokens_override=ctx.max_tokens_override,
    )

    ctx.selected_model = model_sel.model
    ctx.dynamic_max_tokens = model_sel.max_tokens
    ctx.temperature = model_sel.temperature

    # ── 2. Build prompt ─────────────────────────────────────────────
    system_prompt = build_system_prompt(ctx.role, ctx.response_type)
    if intent.is_follow_up:
        system_prompt += " This is a follow-up question — maintain context continuity."
    if intent.is_clarification:
        system_prompt += " User is asking for clarification — be more detailed."

    # Quality context strings
    dq = retrieval.data_quality
    quality_context = (
        f"[{dq.total_chunks} chunks from {dq.total_documents} doc(s), "
        f"confidence: {dq.confidence_score}%]"
    )
    quality_warning = _quality_warning(dq.quality, dq.relevance_quality)

    # Proof snippet
    proof_snippet = _extract_proof_snippet(retrieval)

    # Conversation context for prompt
    conv_ctx = ""
    if ctx.conversation_history:
        parts = []
        for msg in ctx.conversation_history[-5:]:
            role_str = msg.get("role", "user")
            content = msg.get("content", "")
            if role_str in ("user", "assistant") and content:
                parts.append(f"{role_str.capitalize()}: {content}")
        conv_ctx = "\n".join(parts)

    user_prompt = build_answer_prompt(
        answer_mode=intent.answer_mode,
        role=ctx.role,
        response_type=ctx.response_type,
        core_fear=intent.core_fear,
        summaries_toon=retrieval.summaries_toon,
        chunks_toon=retrieval.chunks_toon,
        refined_question=intent.refined_question,
        data_quality=dq.quality,
        quality_context=quality_context,
        quality_warning=quality_warning,
        conversation_context=conv_ctx,
        proof_snippet=proof_snippet,
        selected_solution=getattr(intent, "selected_solution", None),
        solution_rationale=getattr(intent, "solution_rationale", None),
        list_items=list_items,
        is_multi_problem=is_multi_problem,
        is_proof_question=is_proof_question,
        is_discovery_question=is_discovery_question,
        is_comparison=is_comparison,
    )

    # ── 3. LLM call ────────────────────────────────────────────────

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    if ctx.conversation_history:
        for msg in ctx.conversation_history[-5:]:
            r = msg.get("role", "user")
            c = msg.get("content", "")
            if r in ("user", "assistant") and c:
                messages.append({"role": r, "content": c})

    messages.append({"role": "user", "content": user_prompt})

    # --- DEBUG: Log prompt length and preview for TOON effectiveness ---
    prompt_text = system_prompt + "\n" + user_prompt
    logger.info("[TOON DEBUG] Prompt length: %d chars, %d tokens (approx)", len(prompt_text), len(prompt_text) // 4)
    logger.info("[TOON DEBUG] Prompt preview: %s", prompt_text[:500].replace("\n", " "))


    client = get_openai_client()
    response = client.chat.completions.create(
        model=model_sel.model,
        messages=messages,
        max_tokens=model_sel.max_tokens,
        temperature=model_sel.temperature,
    )
    if not response.choices or not response.choices[0].message.content:
        logger.warning("Answer LLM returned no content; using fallback message.")
        answer = (
            "I couldn't generate a full answer from the retrieved context. "
            "Try rephrasing or asking about a specific document or metric."
        )
    else:
        answer = response.choices[0].message.content.strip() or (
            "I don't have enough relevant content to answer that. "
            "Please try a more specific question or check the knowledge base."
        )

    logger.info("Answer generated: %d chars, model=%s", len(answer), model_sel.model)

    # --- Token usage and TOON savings tracking ---
    # Example: OpenAI API returns usage in response.usage
    token_usage = None
    if hasattr(response, "usage") and response.usage:
        token_usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
            "completion_tokens": getattr(response.usage, "completion_tokens", None),
            "total_tokens_used": getattr(response.usage, "total_tokens", None),
        }
    # Pass toon_savings from retrieval result if available
    toon_savings = getattr(retrieval, "toon_savings", None)
    ctx.token_usage = token_usage
    ctx.toon_savings = toon_savings

    # ── 4. Quality evaluation ───────────────────────────────────────
    quality = evaluate_quality(
        answer=answer,
        role=ctx.role,
        response_type=ctx.response_type,
        answer_mode=intent.answer_mode,
        intent_decision=intent,
        retrieval_result=retrieval,
    )

    # ── 5. Refinement gate ──────────────────────────────────────────
    if quality.needs_refinement:
        logger.info(
            "Quality score %.1f/20 — triggering refinement (failed: %s)",
            quality.weighted_total, quality.failed_checks,
        )
        answer = await _refine_answer(
            answer, messages, quality, ctx, model_sel,
        )
        # Re-evaluate after refinement
        quality = evaluate_quality(
            answer=answer,
            role=ctx.role,
            response_type=ctx.response_type,
            answer_mode=intent.answer_mode,
            intent_decision=intent,
            retrieval_result=retrieval,
        )

    # ── 6. Build final response ─────────────────────────────────────
    sources = _build_sources(retrieval)
    followups = _build_followups(retrieval, intent)

    return FinalResponse(
        answer=answer,
        sources=sources,
        followups=followups,
        quality_score=quality,
        pipeline_metadata=PipelineMetadata(
            intent=intent.intent,
            attribute=intent.attribute,
            answer_mode=intent.answer_mode,
            model_used=model_sel.model,
            collections_searched=intent.selected_collections,
            documents_found=len(retrieval.documents),
            chunks_retrieved=len(retrieval.chunks),
            data_quality=dq.quality,
            confidence_score=dq.confidence_score,
            selected_solution=getattr(intent, "selected_solution", None),
        ),
        token_usage=getattr(ctx, "token_usage", None),
        toon_savings=getattr(ctx, "toon_savings", None),
    )


# ── Quality evaluator ───────────────────────────────────────────────

def evaluate_quality(
    answer: str,
    role: str,
    response_type: str,
    answer_mode: str,
    intent_decision: IntentDecision,
    retrieval_result: RetrievalResult,
) -> QualityScore:
    """
    Evaluate response against the 5 weighted sales quality criteria.

    Returns a QualityScore with per-dimension scores and weighted total.
    """
    a = (answer or "").lower()
    passed: list[str] = []
    failed: list[str] = []

    # ── Accuracy (weight=5) ─────────────────────────────────────────
    accuracy = 5
    for phrase in BANNED_PHRASES:
        if phrase.lower() in a:
            accuracy -= 1
            failed.append("accuracy:banned_phrase")
            break
    if not any(w in a for w in ["source:", "source :"]):
        accuracy -= 1
        failed.append("accuracy:no_source_line")
    else:
        passed.append("accuracy:has_source_line")
    accuracy = max(0, accuracy)
    if accuracy >= 4:
        passed.append("accuracy:grounded")

    # ── Relevancy (weight=5) ────────────────────────────────────────
    relevancy = 4  # Start at 4, adjust
    # Check if answer addresses the entity
    if intent_decision.entity:
        if intent_decision.entity.lower() not in a:
            relevancy -= 1
            failed.append("relevancy:entity_missing")
        else:
            passed.append("relevancy:entity_present")
    if relevancy >= 4:
        passed.append("relevancy:addresses_question")

    # ── Completeness (weight=4) ─────────────────────────────────────
    completeness = 3  # Start at 3
    if response_type == RESPONSE_TYPES.get("SALES_RECOMMENDATION"):
        sections = ["recommendation", "why", "how", "proof"]
        found = sum(1 for s in sections if s in a)
        if found >= 3:
            completeness = 5
            passed.append("completeness:rwph_present")
        elif found >= 2:
            completeness = 4
            passed.append("completeness:partial_rwph")
        else:
            failed.append("completeness:missing_sections")
    # CTA check
    cta_phrases = [
        "would you like", "shall i", "let me know",
        "i can", "next step", "ready to",
    ]
    if any(p in a for p in cta_phrases):
        completeness = min(5, completeness + 1)
        passed.append("completeness:has_cta")
    else:
        failed.append("completeness:no_cta")

    # ── Clarity (weight=3) ──────────────────────────────────────────
    clarity = 4
    bullet_lines = [ln for ln in answer.splitlines() if ln.strip().startswith(("•", "- ", "* "))]
    if len(bullet_lines) > 6:
        clarity -= 1
        failed.append("clarity:too_many_bullets")
    else:
        passed.append("clarity:bullet_cap_ok")
    # Mode-appropriate length
    word_count = len(answer.split())
    if answer_mode == "brief" and word_count > 100:
        clarity -= 1
        failed.append("clarity:brief_too_long")

    # ── Sales Maturity (weight=3) ───────────────────────────────────
    sales_maturity = 3
    # Experience framing
    exp_phrases = ["we have seen", "in our experience", "typically", "we recommend"]
    if any(p in a for p in exp_phrases):
        sales_maturity += 1
        passed.append("sales_maturity:experience_framing")
    else:
        failed.append("sales_maturity:no_experience_framing")
    # Banned phrases (overlap with accuracy)
    if any(bp.lower() in a for bp in BANNED_PHRASES):
        sales_maturity -= 1
        failed.append("sales_maturity:banned_phrase")
    # Hedging
    hedging = ["i think", "it might be", "perhaps", "it seems"]
    if any(h in a for h in hedging):
        sales_maturity -= 1
        failed.append("sales_maturity:hedging")
    else:
        passed.append("sales_maturity:confident_tone")
    sales_maturity = max(0, min(5, sales_maturity))

    return QualityScore(
        accuracy=accuracy,
        relevancy=relevancy,
        completeness=completeness,
        clarity=clarity,
        sales_maturity=sales_maturity,
        passed_checks=passed,
        failed_checks=failed,
    )


# ── Refinement ──────────────────────────────────────────────────────

async def _refine_answer(
    answer: str,
    original_messages: list[dict],
    quality: QualityScore,
    ctx: PipelineContext,
    model_sel: Any,
) -> str:
    """One-shot refinement pass targeting specific failed criteria."""
    detail = (
        f"Weighted score: {quality.weighted_total:.1f}/20. "
        f"Failed checks: {', '.join(quality.failed_checks)}"
    )
    instruction = build_refinement_instruction(
        failed_checks=quality.failed_checks,
        role=ctx.role,
        response_type=ctx.response_type,
        quality_detail=detail,
    )

    messages = list(original_messages)
    messages.append({"role": "assistant", "content": answer})
    messages.append({"role": "user", "content": instruction})

    client = get_openai_client()
    refine_max = max(512, min(model_sel.max_tokens, 2048))
    response = client.chat.completions.create(
        model=model_sel.model,
        messages=messages,
        max_tokens=refine_max,
        temperature=0.5,
    )
    refined = response.choices[0].message.content.strip()
    logger.info("Refinement complete: %d chars", len(refined))
    return refined


# ── Helpers ─────────────────────────────────────────────────────────

def _quality_warning(quality: str, relevance: str) -> str:
    """Build a quality warning string for the prompt."""
    parts = []
    if quality == "insufficient":
        parts.append("\nLIMITED DATA: The retrieved data is limited. Acknowledge information gaps if they exist.")
    elif quality == "excellent":
        parts.append("\nRICH DATA: You have comprehensive data from quality sources. Provide detailed answer.")
    elif quality == "good":
        parts.append("\nSOLID DATA: You have good coverage across relevant documents.")
    else:
        parts.append("\nADEQUATE DATA: Work with available information; note any limitations transparently.")

    hints = {
        "high": "Excellent chunk relevance - chunks are highly relevant to the question",
        "medium": "Moderate chunk relevance - chunks are reasonably relevant",
        "low": "Low chunk relevance - chunks may have mixed relevance",
        "very_low": "Very low chunk relevance - be cautious about answer confidence",
    }
    if relevance in hints:
        parts.append(f"\n{hints[relevance]}")

    return "".join(parts)


def _extract_proof_snippet(retrieval: RetrievalResult) -> str | None:
    """Extract a concise proof snippet from retrieved chunks."""
    if not retrieval.chunks:
        return None

    domain = scale = problem = outcome = None

    for c in retrieval.chunks:
        text = (c.chunk_text + " " + c.document_title).lower()

        if not domain:
            for d in ["fintech", "health", "healthcare", "bfsi", "bank", "finance", "saas"]:
                if d in text:
                    domain = d.capitalize()
                    break

        if not scale:
            m = re.search(
                r"(\d[\d,\.]*\s*(?:users|customers|transactions|tests|nodes|endpoints|devices))",
                text,
            )
            if m:
                scale = m.group(1)

        if not problem:
            for p in ["bug", "crash", "flaky", "failure", "downtime", "compliance", "slow", "latency"]:
                if p in text:
                    problem = p
                    break

        if not outcome:
            m2 = re.search(
                r"(reduc(?:ed|tion) of\s+\d+%|\d+%\s+(?:reduction|improvement|increase)"
                r"|achieved\s+\d+%|improved by\s+\d+%|zero\s+breach)",
                text,
            )
            if m2:
                outcome = m2.group(1)

    if not any([domain, scale, problem, outcome]):
        return None

    parts = []
    if domain:
        parts.append(f"Domain: {domain}")
    if scale:
        parts.append(f"Scale: {scale}")
    if problem:
        parts.append(f"Problem: {problem}")
    if outcome:
        parts.append(f"Outcome: {outcome}")

    # Top source titles
    titles = []
    for c in retrieval.chunks:
        t = humanize_title(c.document_title)
        if t and t not in titles:
            titles.append(t)
        if len(titles) >= 2:
            break

    source = f"Source: {titles[0]}" if titles else "Source: [document]"
    if len(titles) > 1:
        source = "Sources: " + ", ".join(titles[:2])

    return " | ".join(parts) + "\n" + source


def _build_sources(retrieval: RetrievalResult) -> list[str] | None:
    """Build a list of top-3 source document titles."""
    if not retrieval.documents:
        return None
    sources: list[str] = []
    for d in retrieval.documents[:3]:
        title = humanize_title(d.title)
        if title and title not in sources:
            sources.append(title)
    return sources or None


def _build_followups(
    retrieval: RetrievalResult,
    intent: IntentDecision,
) -> list[dict] | None:
    """Generate 1-3 concise follow-up suggestions."""
    suggestions: list[dict] = []

    si = intent.sales_intent
    if si == "Discovery":
        suggestions.append({
            "text": "Would you like a short checklist to stabilize production quickly?",
            "type": "offer_checklist",
        })
        suggestions.append({
            "text": "Shall I propose a 2-4 week pilot to stabilize critical flows?",
            "type": "offer_pilot",
        })
    elif si == "Solutioning":
        suggestions.append({
            "text": "Do you want an implementation plan or integration steps?",
            "type": "ask_deep",
        })
    elif si == "Decision":
        suggestions.append({
            "text": "Shall I prepare a proposal and pricing estimate?",
            "type": "request_pricing",
        })

    # Low confidence
    conf = retrieval.data_quality.confidence_score
    if conf < 60:
        suggestions.insert(0, {
            "text": "I can dig deeper — would you like more detailed evidence or examples?",
            "type": "offer_deeper",
        })

    # Deduplicate and cap
    seen: set[str] = set()
    final: list[dict] = []
    for s in suggestions:
        if s["text"] not in seen:
            seen.add(s["text"])
            final.append(s)
        if len(final) >= 3:
            break

    return final or None
