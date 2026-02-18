"""
Pipeline Stage 1b: LLM-based query rewriting + collection selection.

Single focused prompt replacing the 100-line mega-prompt.  Only called
when the intent is FACTUAL (i.e. after metadata short-circuit check).
"""

from __future__ import annotations

import json
from typing import Any

from app.schemas.intent import IntentDecision
from app.services.llm import get_openai_client, count_tokens, convert_to_toon
from app.prompts.collection_selector import build_collection_selector_prompt
from app.prompts.constants import DEFAULT_MODEL_MINI
from app.utils.text import normalize_collection_name
from app.utils.logging import get_logger

logger = get_logger("askmojo.pipeline.query_rewrite")


async def rewrite_and_select(
    intent_decision: IntentDecision,
    categories_data: list[dict[str, Any]],
    conversation_history: list[dict] | None = None,
) -> IntentDecision:
    """
    Call the LLM to refine the user's question and select relevant
    collections.  Mutates and returns the IntentDecision in-place.

    This replaces the old Step 1 mega-prompt (routes.py lines 1259-1356).

    Returns:
        The same IntentDecision, enriched with:
          - refined_question
          - selected_collections
          - answer_mode (may be updated by LLM)
          - proceed_to_retrieval flag
          - short_circuit_answer (if LLM says not to proceed)
    """
    if not categories_data:
        intent_decision.proceed_to_retrieval = False
        intent_decision.short_circuit_answer = (
            "No categories are available. Please create categories "
            "first before asking questions."
        )
        return intent_decision

    # Build categories description for the prompt
    categories_desc = _build_categories_description(categories_data)

    # Build conversation context
    conv_context = None
    if conversation_history:
        parts = []
        for msg in conversation_history[-3:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"{role.capitalize()}: {content}")
        conv_context = "\n".join(parts)

    # Build focused prompt
    system_prompt, user_prompt = build_collection_selector_prompt(
        question=intent_decision.refined_question,
        categories_description=categories_desc,
        entity=intent_decision.entity,
        conversation_context=conv_context,
    )

    # Calculate dynamic max tokens
    max_tokens = _calculate_max_tokens(len(categories_data), intent_decision)

    # Call LLM
    client = get_openai_client()
    try:
        response = client.chat.completions.create(
            model=DEFAULT_MODEL_MINI,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as e:
        logger.warning("Collection selector LLM call failed: %s. Using fallback.", e)
        _apply_fallback_collections(intent_decision, categories_data)
        return intent_decision

    # Parse response — guard against empty choices or invalid JSON
    if not response.choices:
        logger.warning("Collection selector returned no choices. Using fallback.")
        _apply_fallback_collections(intent_decision, categories_data)
        return intent_decision

    raw = response.choices[0].message.content
    if not raw or not raw.strip():
        logger.warning("Collection selector returned empty content. Using fallback.")
        _apply_fallback_collections(intent_decision, categories_data)
        return intent_decision

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Collection selector returned invalid JSON: %s. Using fallback.", e)
        _apply_fallback_collections(intent_decision, categories_data)
        return intent_decision

    if not isinstance(data, dict):
        logger.warning("Collection selector response was not a dict. Using fallback.")
        _apply_fallback_collections(intent_decision, categories_data)
        return intent_decision

    # Update IntentDecision
    selected = data.get("selected_collections", [])
    if not isinstance(selected, list):
        selected = []
    # Filter out master_docs if incorrectly selected
    selected = [c for c in selected if c != "master_docs"]

    # Validate collections against known names
    valid_names = {d["collection_name"] for d in categories_data}
    norm_to_valid = {normalize_collection_name(n): n for n in valid_names}

    validated: list[str] = []
    for coll in selected:
        if coll in valid_names:
            validated.append(coll)
        else:
            norm = normalize_collection_name(coll)
            if norm in norm_to_valid:
                validated.append(norm_to_valid[norm])
            else:
                logger.warning("LLM selected unknown collection: %s", coll)

    intent_decision.selected_collections = validated
    refined = data.get("refined_question", intent_decision.refined_question)
    if isinstance(refined, str) and refined.strip():
        intent_decision.refined_question = refined.strip()

    # Update answer mode if LLM provided one
    llm_mode = data.get("answer_mode")
    if llm_mode in ("extract", "brief", "summarize", "explain"):
        intent_decision.answer_mode = llm_mode

    # Handle non-proceed case
    proceed = data.get("proceed_to_step2", True)
    if proceed is False or (isinstance(proceed, str) and proceed.lower() in ("false", "no", "0")):
        intent_decision.proceed_to_retrieval = False
        direct = data.get("direct_answer")
        intent_decision.short_circuit_answer = direct if isinstance(direct, str) and direct.strip() else None

    logger.info(
        "Rewrite complete: collections=%s mode=%s proceed=%s",
        validated, intent_decision.answer_mode, proceed,
    )

    return intent_decision


def _apply_fallback_collections(
    intent_decision: IntentDecision,
    categories_data: list[dict[str, Any]],
) -> None:
    """
    On LLM failure or invalid response, set safe defaults so the pipeline
    can continue (e.g. use all collections, proceed to retrieval).
    """
    valid_names = [d["collection_name"] for d in categories_data]
    intent_decision.selected_collections = valid_names if valid_names else []
    intent_decision.proceed_to_retrieval = True
    intent_decision.short_circuit_answer = None
    logger.info("Fallback: selected_collections=%s, proceed_to_retrieval=True", intent_decision.selected_collections)


def _build_categories_description(categories_data: list[dict]) -> str:
    """Build a human-readable description of available categories."""
    lines = []
    for cat in categories_data:
        domains = ", ".join(cat.get("domains", [])) or "N/A"
        desc = cat.get("description", "No description available")
        lines.append(
            f"- Collection: {cat['collection_name']} "
            f"(Category: {cat.get('category_name', 'N/A')}, "
            f"Domains: {domains})\n"
            f"  Description: {desc}"
        )
    return "\n".join(lines)


def _calculate_max_tokens(
    num_categories: int,
    intent_decision: IntentDecision,
) -> int:
    """Dynamically calculate max tokens for the collection selector LLM call."""
    base = 400
    category_factor = min(num_categories * 30, 500)
    query_complexity = min(len(intent_decision.refined_question) / 30, 300)
    context_factor = min(intent_decision.conversation_length * 40, 200)
    total = base + category_factor + int(query_complexity) + int(context_factor)
    return max(500, min(total, 4096))
