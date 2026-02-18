"""
Text utilities extracted from routes.py:
  - Entity extraction from questions
  - Title humanization (internal filenames → readable names)
  - Collection name normalization
  - Document type inference from questions
  - Core fear detection

All functions are pure (no I/O, no DB, no LLM).
"""

from __future__ import annotations

import re


# ── Non-entity words (used by entity extractor) ─────────────────────
_NON_ENTITIES = frozenset({
    "DR", "QA", "CI", "CD", "API", "AWS", "GCP", "Azure", "EKS",
    "The", "This", "That", "What", "Which", "How", "Where", "When",
    "Who", "Why", "Does", "Is", "Are", "Can", "Could", "Should",
    "Would", "Will", "Do", "Has", "Have", "Had", "An", "No", "Yes",
    "Full", "All", "Any", "Each", "Every", "My", "Our", "Your",
})

# ── Known solution title mappings ────────────────────────────────────
_KNOWN_TITLE_MAP: dict[str, str] = {
    "bugbuster": "BugBuster",
    "fastrack automation": "Fastrack Automation",
    "codeprobe": "CodeProbe",
    "moolyaimpact": "MoolyAImpact",
    "continuouscare": "ContinuousCare",
}


def extract_entity(question: str) -> str | None:
    """
    Extract primary entity / company / project name from the question.

    Uses heuristic patterns: possessives (X's), "for X", "about X", "in X".
    Returns the entity name or None.
    """
    q = (question or "").strip()
    if not q:
        return None

    # Pattern 1: "<Entity>'s" (possessive) — strongest signal
    match = re.search(r"([A-Z][a-zA-Z0-9_\-]+)'s\b", q)
    if match and match.group(1) not in _NON_ENTITIES:
        return match.group(1)

    # Pattern 2: "for <Entity>" / "about <Entity>" / "in <Entity>" / "of <Entity>"
    match = re.search(r"(?:for|about|in|of|at|from|by)\s+([A-Z][a-zA-Z0-9_\-]+)", q)
    if match and match.group(1) not in _NON_ENTITIES:
        return match.group(1)

    # Pattern 3: Look for CamelCase or unusual capitalised words (not at sentence start)
    words = q.split()
    for i, word in enumerate(words):
        if i == 0:
            continue  # Skip first word (always capitalised)
        clean = word.strip("?.,!\"'()")
        if clean and clean[0].isupper() and clean not in _NON_ENTITIES and len(clean) > 2:
            return clean

    return None


def humanize_title(title: str | None) -> str | None:
    """
    Convert internal-looking filenames to human-friendly names.

    Examples:
        "BugBuster_Solutions" → "BugBuster"
        "Fastrack Automation Presentation (1)" → "Fastrack Automation"
        "MoolyAImpact - Updated (1)" → "MoolyAImpact"
    """
    if not title:
        return title
    t = title.strip()
    # Remove file extensions
    t = re.sub(r"\.(pdf|docx?|pptx?)$", "", t, flags=re.IGNORECASE)
    # Replace underscores/dashes with spaces
    t = t.replace("_", " ")
    # Remove common suffix words
    t = re.sub(r"\b(Solutions?|Presentation|Report|Updated)\b", "", t, flags=re.IGNORECASE)
    # Remove parenthetical counters like (1), (2)
    t = re.sub(r"\s*\(\d+\)$", "", t)
    # Normalize whitespace
    t = re.sub(r"\s{2,}", " ", t).strip()
    # Known mappings
    key = re.sub(r"\s+", " ", t).lower()
    if key in _KNOWN_TITLE_MAP:
        return _KNOWN_TITLE_MAP[key]
    return t or title


def normalize_collection_name(name: str) -> str:
    """
    Normalize a category name into a ChromaDB collection name.

    Replaces the 6+ scattered `.lower().replace(" ", "_").replace("-", "_")`
    calls in the old routes.py.
    """
    return (name or "").lower().replace(" ", "_").replace("-", "_")


def infer_doc_type_from_question(question: str) -> str | None:
    """
    Heuristic mapping from user question to a preferred document type.

    Returns: "proposal" | "case_study" | "solution" | None
    """
    q = (question or "").lower()
    if "case study" in q or "case studies" in q or "success story" in q or "success stories" in q:
        return "case_study"
    if "proposal" in q or "proposals" in q:
        return "proposal"
    if "solution" in q or "solutions" in q or "service" in q or "services" in q:
        return "solution"
    return None


def infer_core_fear(question: str) -> str | None:
    """
    Detect the user's core concern/fear: cost, speed, risk, scale, or trust.
    """
    q = (question or "").lower()
    if any(w in q for w in ["cost", "expensive", "budget", "price", "afford", "financial", "roi", "investment"]):
        return "cost"
    if any(w in q for w in ["slow", "fast", "speed", "quick", "quickly", "delay", "time", "acceleration", "reduce time"]):
        return "speed"
    if any(w in q for w in ["risk", "fail", "crash", "bug", "defect", "stability", "reliable", "safe", "security", "confidence", "unstable"]):
        return "risk"
    if any(w in q for w in ["scale", "grow", "large", "handle", "capacity", "volume", "load", "users", "growth", "concurrent"]):
        return "scale"
    if any(w in q for w in ["proof", "evidence", "customer", "case study", "success", "track record", "experience", "credentials"]):
        return "trust"
    return None


def infer_answer_mode(question: str) -> str:
    """
    Infer the answer mode from question structure.

    Returns: "extract" | "brief" | "summarize" | "explain"
    """
    q = (question or "").lower()

    # Extract mode: user wants a specific value
    extract_kws = ["what is the", "what's the", "what are the", "how much", "how many", "percentage", "number of"]
    if any(kw in q for kw in extract_kws):
        return "extract"

    # Brief mode: yes/no or short answer
    brief_kws = ["is there", "do we have", "does it", "can we", "is it possible", "are there"]
    if any(kw in q for kw in brief_kws):
        return "brief"

    # Summarize mode: user wants an overview
    summarize_kws = ["summarize", "summary", "overview", "list", "outline", "key points"]
    if any(kw in q for kw in summarize_kws):
        return "summarize"

    # Default: explain
    return "explain"
