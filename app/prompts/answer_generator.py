"""
Prompt templates for Stage 3: Answer generation.

Each answer mode has its own focused template.
All templates end with the quality self-evaluation checklist.
"""

from __future__ import annotations

from app.prompts.constants import (
    QUALITY_SELF_EVAL_CHECKLIST,
    BANNED_PHRASES,
    build_behavioral_directives,
    build_prompt_header,
    build_context_blocks,
)


def build_system_prompt(role: str, response_type: str) -> str:
    """Build the static system message (persona / role)."""
    if role == "Pre-Sales":
        return (
            "You are a Senior Pre-Sales Engineer with deep technical knowledge. "
            "You answer with precise technical details, evidence from documents, "
            "and use a professional, consultative tone. "
            "You never fabricate data. You cite sources using human-friendly document titles."
        )
    return (
        "You are a Senior Sales Advisor with expertise in enterprise software solutions. "
        "You speak with confidence, lead with recommendations, and always ground "
        "your claims in evidence from documents. "
        "You never fabricate data. You cite sources using human-friendly document titles."
    )


def build_answer_prompt(
    answer_mode: str,
    role: str,
    response_type: str,
    core_fear: str | None,
    summaries_toon: str,
    chunks_toon: str,
    refined_question: str,
    data_quality: str,
    quality_context: str = "",
    quality_warning: str = "",
    conversation_context: str = "",
    proof_snippet: str | None = None,
    selected_solution: str | None = None,
    solution_rationale: str | None = None,
) -> str:
    """
    Build the full user-message prompt for answer generation.

    Structure:
      1. Prompt header (mode, role, response type)
      2. Behavioral directives
      3. Mode-specific instructions
      4. Solution constraint (Option C: recommend only this solution)
      5. Context blocks (summaries + chunks + question)
      6. Quality self-evaluation checklist
    """
    parts: list[str] = []

    # 1. Header
    parts.append(build_prompt_header(answer_mode, role, response_type, core_fear))

    # 2. Behavioral directives
    parts.append("## BEHAVIORAL DIRECTIVES")
    parts.append(build_behavioral_directives(role, response_type, core_fear))

    # 3. Mode-specific instructions
    parts.append(_mode_instructions(answer_mode, role, response_type))

    # 4. Option C: Solution constraint (single recommendation, no list)
    if selected_solution:
        constraint = (
            f"## SOLUTION CONSTRAINT\n"
            f"You must recommend only **{selected_solution}**. "
            f"Do not recommend or list other solutions. "
            f"Lead with this recommendation and ground your answer in the provided chunks."
        )
        if solution_rationale:
            constraint += f" Rationale to reflect: {solution_rationale}"
        constraint += "."
        parts.append(constraint)

    # 5. Banned phrases
    ban_str = ", ".join(f'"{p}"' for p in BANNED_PHRASES)
    parts.append(f"## BANNED PHRASES\nNever use: {ban_str}")

    # 6. Proof snippet (if available)
    if proof_snippet:
        parts.append(f"## PROOF EVIDENCE\n{proof_snippet}")

    # 7. Conversation context
    if conversation_context:
        parts.append(f"## CONVERSATION CONTEXT\n{conversation_context}")

    # 8. Context blocks
    parts.append(
        build_context_blocks(
            summaries_toon, chunks_toon, refined_question,
            data_quality, quality_context, quality_warning,
        )
    )

    # 9. Quality self-evaluation
    parts.append(QUALITY_SELF_EVAL_CHECKLIST)

    return "\n\n".join(parts)


def _mode_instructions(answer_mode: str, role: str, response_type: str) -> str:
    """Return mode-specific generation instructions."""
    if answer_mode == "extract":
        return (
            "## INSTRUCTIONS (EXTRACT MODE)\n"
            "- Extract the specific value or fact the user asked for.\n"
            "- Be precise. One sentence if possible.\n"
            "- Cite the source document title.\n"
            "- If not found, say so clearly."
        )

    if answer_mode == "brief":
        return (
            "## INSTRUCTIONS (BRIEF MODE)\n"
            "- Answer in 1-3 sentences maximum.\n"
            "- Lead with yes/no if applicable.\n"
            "- Follow with the key evidence.\n"
            "- Cite the source."
        )

    if answer_mode == "summarize":
        return (
            "## INSTRUCTIONS (SUMMARIZE MODE)\n"
            "- Provide a structured summary with bullet points.\n"
            "- Maximum 5-6 key points.\n"
            "- Group by theme if covering multiple documents.\n"
            "- End with a source line."
        )

    # explain (default)
    if response_type == "SALES_RECOMMENDATION":
        return (
            "## INSTRUCTIONS (EXPLAIN MODE — SALES RECOMMENDATION)\n"
            "Structure your answer as:\n"
            "- **Recommendation**: Clear, actionable recommendation (1-2 sentences)\n"
            "- **Why**: Business impact and rationale (2-3 sentences)\n"
            "- **How**: High-level implementation approach (2-3 sentences)\n"
            "- **Proof**: Evidence from documents (1-2 sentences with citation)\n"
            "- End with a confident CTA.\n"
            "- Maximum 3 bullets per section."
        )

    if response_type == "PROOF_STORY":
        return (
            "## INSTRUCTIONS (EXPLAIN MODE — PROOF STORY)\n"
            "Tell the proof as a change-in-state narrative:\n"
            "- Context → Scale → Problem → Intervention → Outcome\n"
            "- Use ranges (e.g., 20-40%) instead of exact numbers\n"
            "- Cite the source case study/document\n"
            "- End with relevance to the prospect's situation."
        )

    if response_type == "OBJECTION_HANDLING":
        return (
            "## INSTRUCTIONS (EXPLAIN MODE — OBJECTION HANDLING)\n"
            "- Acknowledge the concern\n"
            "- Reframe with data from documents\n"
            "- Offer a risk-reduced alternative (pilot, phased approach)\n"
            "- Cite proof of value\n"
            "- End with a confident CTA."
        )

    return (
        "## INSTRUCTIONS (EXPLAIN MODE)\n"
        "- Provide a clear, structured explanation.\n"
        "- Use headers for multi-section answers.\n"
        "- Cite sources with human-friendly titles.\n"
        "- End with a confident summary."
    )
