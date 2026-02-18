"""
Prompt template for rubric-based answer refinement (Stage 3).

Only invoked when `QualityScore.needs_refinement` is True
(weighted_total < 12/20).
"""

from __future__ import annotations

from app.prompts.constants import RESPONSE_TYPES, BANNED_PHRASES


def build_refinement_instruction(
    failed_checks: list[str],
    role: str,
    response_type: str,
    quality_detail: str = "",
) -> str:
    """
    Produce a concise, targeted refinement instruction citing
    specific failed quality criteria by name and weight.

    Args:
        failed_checks: List of check names that failed (e.g. "no_banned_phrases").
        role: "Sales" or "Pre-Sales".
        response_type: One of RESPONSE_TYPES values.
        quality_detail: Optional extra detail from the quality evaluator
                       (e.g. "Relevancy scored 2/5 because...").
    """
    lines = [
        "Revise your previous answer to satisfy these constraints strictly:",
    ]

    if quality_detail:
        lines.append(f"- Quality feedback: {quality_detail}")

    if response_type == RESPONSE_TYPES.get("SALES_RECOMMENDATION"):
        lines.append(
            "- Ensure sections: Recommendation, Why, How, Proof (short, persuasive)."
        )

    if "no_banned_phrases" in failed_checks:
        ban_str = ", ".join(f'"{p}"' for p in BANNED_PHRASES)
        lines.append(f"- Remove phrases like {ban_str}.")

    if "has_source_line" in failed_checks:
        lines.append(
            "- End with a single 'Source:' line using human-friendly document titles."
        )

    if "bullet_cap_le_6" in failed_checks:
        lines.append("- Keep at most 3 bullets in any list; be concise.")

    if "accuracy" in failed_checks:
        lines.append(
            "- Every claim must be grounded in the provided chunks. "
            "Remove any claim not supported by evidence."
        )

    if "relevancy" in failed_checks:
        lines.append(
            "- Answer the specific QUESTION, not just the topic. "
            "Lead with the direct answer."
        )

    if "completeness" in failed_checks:
        lines.append(
            "- Ensure the sales team can use this without follow-up. "
            "Include Recommendation + Why + How + Proof."
        )

    if "clarity" in failed_checks:
        lines.append(
            "- Improve structure: use headers and bullets. "
            "Brief mode = 1-3 sentences max."
        )

    if "sales_maturity" in failed_checks:
        lines.append(
            "- Use experience framing ('We have seen...'), outcome language, "
            "and a confident CTA. Remove hedging."
        )

    # Always include focus directive
    lines.append(
        "- Focus only on the user's core concern; expose only the single best path."
    )

    return "\n".join(lines)
