"""
Option C: Solution-selection layer.

After collection selection (Stage 1), this step picks the SINGLE best solution
(BugBuster, Fastrack Automation, QA Ownership, MoolyAImpact) for the user's
question. Used to bias retrieval and constrain the answer to one recommendation.

No hardcoding: LLM chooses from solution metadata (scope, constraints).
"""

from __future__ import annotations

import json
from typing import Any

from app.services.llm import get_openai_client
from app.prompts.constants import DEFAULT_MODEL_MINI
from app.utils.logging import get_logger

logger = get_logger("askmojo.pipeline.solution_selector")


# Solution metadata for the LLM: scope (what it's for), constraints (when it fits), not_for (when to avoid)
SOLUTION_METADATA: dict[str, dict[str, Any]] = {
    "BugBuster": {
        "scope": "Production bugs, hotfixes, stability, urgent defects, customer complaints, critical defects",
        "constraints": "Speed to stabilize, risk reduction, incident response",
        "not_for": "Strategy, process audit, flaky tests, team scaling, automation setup",
    },
    "Fastrack Automation": {
        "scope": "Flaky tests, test automation, CI/CD, regression time, slow release, pipeline speed",
        "constraints": "Speed, automation, release velocity",
        "not_for": "Production incidents, strategy, hiring, governance",
    },
    "QA Ownership": {
        "scope": "Hiring, team size, resource crunch, managing QA, outsourcing, scaling QA team",
        "constraints": "Scale, ownership, resource planning",
        "not_for": "Technical automation, production bugs, strategy consulting",
    },
    "MoolyAImpact": {
        "scope": "Process audit, strategy, transformation, AI adoption, consulting, process chaos, modernize",
        "constraints": "Governance, strategy advice, what should we do",
        "not_for": "Tactical fixes, flaky tests, production bugs, hiring, specific tooling",
    },
}


def _build_solution_list_for_prompt() -> str:
    """Build a concise list of solutions and their metadata for the LLM prompt."""
    lines = []
    for name, meta in SOLUTION_METADATA.items():
        scope = meta.get("scope", "—")
        constraints = meta.get("constraints", "—")
        not_for = meta.get("not_for", "—")
        lines.append(
            f"- **{name}**: Scope: {scope}. Fits when: {constraints}. Do NOT recommend for: {not_for}."
        )
    return "\n".join(lines)


async def select_solution(
    question: str,
    selected_collections: list[str],
    answer_mode: str,
) -> tuple[str | None, str | None]:
    """
    Call LLM to pick the single best solution for the user's question.

    Only runs when the question needs a recommendation (explain/summarize).
    Skips for extract/brief to avoid extra latency when user wants a single fact.

    Returns:
        (selected_solution, rationale) or (None, None) if skipped/failed.
    """
    # Skip for modes that don't need a solution recommendation
    if answer_mode in ("extract", "brief"):
        logger.info("[SOLUTION_SELECTOR] Skipping (answer_mode=%s)", answer_mode)
        return None, None

    solution_list = _build_solution_list_for_prompt()
    system_prompt = (
        "You are a sales solution router. Given the user's question and the list of available solutions, "
        "you must pick the ONE solution that best fits the user's stated problem or need. "
        "Output only valid JSON. Do not recommend multiple solutions. "
        "If the question is generic or no solution clearly fits, choose the single best match or return null."
    )
    user_prompt = f"""## AVAILABLE SOLUTIONS
{solution_list}

## USER QUESTION
{question}

## SELECTED COLLECTIONS (for context)
{', '.join(selected_collections) if selected_collections else 'All'}

## OUTPUT (JSON only)
{{"selected_solution": "<exact name from list or null>", "rationale": "<one sentence why>"}}
"""

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=DEFAULT_MODEL_MINI,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=200,
        )
    except Exception as e:
        logger.warning("[SOLUTION_SELECTOR] LLM call failed: %s", e)
        return None, None

    raw = response.choices[0].message.content if response.choices else None
    if not raw or not raw.strip():
        return None, None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[SOLUTION_SELECTOR] Invalid JSON from LLM")
        return None, None

    selected = data.get("selected_solution")
    rationale = data.get("rationale")
    if not selected or not isinstance(selected, str):
        logger.info("[SOLUTION_SELECTOR] LLM returned no solution")
        return None, None

    selected = selected.strip()
    # Normalize to a known solution name
    valid = set(SOLUTION_METADATA.keys())
    if selected not in valid:
        for name in valid:
            if name.lower() in selected.lower() or selected.lower() in name.lower():
                selected = name
                break
        else:
            logger.warning("[SOLUTION_SELECTOR] Unknown solution name: %s", selected)
            return None, None

    rationale = rationale.strip() if isinstance(rationale, str) and rationale else ""
    logger.info("[SOLUTION_SELECTOR] Selected: %s | %s", selected, rationale[:80] if rationale else "")
    return selected, rationale or None
