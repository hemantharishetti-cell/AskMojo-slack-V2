"""
Centralized constants for prompts, banned phrases, response types,
behavioral directives, and quality self-evaluation checklist.

Extracted from routes.py to eliminate scattered hardcoding.
"""

from __future__ import annotations


# ── Response types ──────────────────────────────────────────────────
RESPONSE_TYPES: dict[str, str] = {
    "SALES_RECOMMENDATION": "SALES_RECOMMENDATION",
    "PROOF_STORY": "PROOF_STORY",
    "OBJECTION_HANDLING": "OBJECTION_HANDLING",
    "EXPLANATION": "EXPLANATION",
    "COMPARISON": "COMPARISON",
    "END_TO_END_STORY": "END_TO_END_STORY",
}


# ── Banned phrases (universal) ──────────────────────────────────────
BANNED_PHRASES: list[str] = [
    "According to",
    "The document says",
    "as per the document",
]


# ── Model defaults ──────────────────────────────────────────────────
DEFAULT_MODEL_MINI = "gpt-4o-mini"
DEFAULT_MODEL_FULL = "gpt-4o"
MODEL_SCORE_THRESHOLD_FULL = 3  # score >= this → use gpt-4o


# ── Quality self-evaluation checklist ────────────────────────────────
#    Embedded at the end of every answer-generation prompt.
#    Costs zero additional tokens (part of existing prompt) but
#    dramatically improves first-pass quality.
QUALITY_SELF_EVAL_CHECKLIST = """
BEFORE SUBMITTING YOUR ANSWER, verify against these criteria:
1. ACCURACY (critical, weight 5): Every claim is from the provided chunks. No cross-document mixing. Source cited.
2. RELEVANCY (critical, weight 5): You answered the QUESTION, not just the topic. Right solution for the stated problem.
3. COMPLETENESS (weight 4): Sales team can use this answer without follow-up. Recommendation + Why + How + Proof present.
4. CLARITY (weight 3): Structured with headers/bullets. Brief mode = 1-3 sentences. No jargon dumps.
5. SALES MATURITY (weight 3): Experience framing, outcome language, confident CTA. No "According to..." phrasing.
""".strip()


# ── Behavioral directives ───────────────────────────────────────────
def build_behavioral_directives(
    role: str,
    response_type: str,
    core_fear: str | None = None,
) -> str:
    """
    Directive block encoding the delta checklist.
    Guides first-pass behavior without hard-coding outputs.
    """
    directives = [
        "- Infer the user's core concern (cost, speed, risk, scale, trust) and answer ONLY that concern.",
        "- Internally rank possible paths; expose ONLY the single best path (no lists of solutions).",
        "- Prefer impact language (outcomes/results) over capability listings.",
        "- Ensure completeness as a sequence: Start → Control → Outcome, and include one concrete dimension (scale, timeline, risk removed, or confidence gained).",
        "- Match depth to audience; keep 'how' abstract unless explicitly asked; use analogy/contrast over feature lists.",
        "- Treat proof as change-in-state: Context → Scale → Problem → Intervention → Outcome. Use ranges (e.g., 20–40%) instead of exact numbers.",
        "- Limit to at most 3 bullets/examples; stop once a decision is enabled; end with a confident, actionable CTA.",
        "- Universal checks: answer the question (not the topic), choose one clear path, end with confidence, sound natural in a live sales call.",
    ]

    if core_fear:
        fear_guidance = (
            f"- Primary concern detected: {core_fear.upper()}. "
            f"Prioritize messaging around {core_fear} impact."
        )
        directives.insert(0, fear_guidance)

    return "\n".join(directives)


# ── Role selection ──────────────────────────────────────────────────
def select_role(intent: str, intent_hints: dict | None) -> str:
    """Assign role based on intent: Sales (default) or Pre-Sales."""
    si = (intent_hints or {}).get("sales_intent")
    if si == "Proof":
        return "Pre-Sales"
    return "Sales"


# ── Response type selection ─────────────────────────────────────────
def select_response_type(
    intent: str,
    role: str,
    question: str,
    intent_hints: dict | None,
) -> str:
    """
    Pick the response type from RESPONSE_TYPES based on intent,
    role, and question keywords.
    """
    q = (question or "").lower()
    si = (intent_hints or {}).get("sales_intent")

    if si == "Objection":
        return RESPONSE_TYPES["OBJECTION_HANDLING"]
    if "compare" in q or " vs " in q:
        return RESPONSE_TYPES["COMPARISON"]
    if si == "Proof":
        return RESPONSE_TYPES["PROOF_STORY"]
    if role == "Sales":
        return RESPONSE_TYPES["SALES_RECOMMENDATION"]
    return RESPONSE_TYPES["EXPLANATION"]


# ── Constraints builder ─────────────────────────────────────────────
def build_constraints(role: str, response_type: str) -> dict:
    """Build the constraints dict passed into the prompt builder."""
    return {
        "banned_phrases": BANNED_PHRASES,
        "avoid_internal_filenames": True,
        "experience_framing": role == "Sales",
        "structure": response_type,
    }


# ── Prompt header ───────────────────────────────────────────────────
def build_prompt_header(
    answer_mode: str,
    role: str,
    response_type: str,
    core_fear: str | None = None,
) -> str:
    """Build the prompt header block consistently."""
    header = (
        "\n\n"
        + f"## QUESTION TYPE: {answer_mode.upper()}\n"
        + f"## ROLE: {role}\n"
        + f"## RESPONSE TYPE: {response_type}\n"
    )
    if core_fear:
        header += f"## CORE CONCERN: {core_fear.upper()}\n"
    return header


# ── Context block builder ───────────────────────────────────────────
def build_context_blocks(
    summaries_toon_str: str,
    chunks_toon_str: str,
    refined_question: str,
    data_quality: str,
    quality_context: str,
    quality_warning: str,
) -> str:
    """Assemble the Step-4 context area for the LLM prompt."""
    dq_label = (data_quality or "sufficient").upper()
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## DATA QUALITY: {dq_label} {quality_context}{quality_warning}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## DOCUMENT SUMMARIES
{summaries_toon_str}

## DOCUMENT CHUNKS (Your ONLY source material)
{chunks_toon_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## USER QUESTION
{refined_question}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
