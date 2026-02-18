"""
Prompt template for Stage 1: Collection selection + query rewriting.

This is a SINGLE focused prompt replacing the 100-line mega-prompt
that previously combined 5 responsibilities.

Responsibilities:
  1. Select relevant collections
  2. Rewrite/refine the user query
  3. Determine answer mode

NOT in scope: relevance checking, direct answer generation.
"""

from __future__ import annotations


def build_collection_selector_prompt(
    question: str,
    categories_description: str,
    entity: str | None = None,
    conversation_context: str | None = None,
) -> tuple[str, str]:
    """
    Build the system and user prompts for collection selection.

    Returns:
        (system_prompt, user_prompt)
    """
    system_prompt = (
        "You are a precise document routing assistant. "
        "Your job is to analyze the user's question and decide:\n"
        "1. Which document collections are relevant\n"
        "2. A refined version of the question optimized for vector search\n"
        "3. The answer mode (extract/brief/summarize/explain)\n\n"
        "Output ONLY valid JSON with this schema:\n"
        "{\n"
        '  "selected_collections": ["collection_name_1", ...],\n'
        '  "refined_question": "optimized search query",\n'
        '  "answer_mode": "extract|brief|summarize|explain",\n'
        '  "reasoning": "one sentence explaining your choice"\n'
        "}\n\n"
        "Rules:\n"
        "- Select 1-3 most relevant collections. Prefer fewer.\n"
        "- If the question mentions a specific document/entity, include its collection.\n"
        "- The refined question should be concise and search-optimized.\n"
        "- Do NOT add information the user didn't ask about.\n"
    )

    user_parts = [f"## AVAILABLE COLLECTIONS\n{categories_description}"]

    if entity:
        user_parts.append(f"## DETECTED ENTITY: {entity}")

    if conversation_context:
        user_parts.append(f"## CONVERSATION CONTEXT\n{conversation_context}")

    user_parts.append(f"## USER QUESTION\n{question}")

    user_prompt = "\n\n".join(user_parts)

    return system_prompt, user_prompt
