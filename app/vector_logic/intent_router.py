"""
Step 0: Intent-Based Router for the RAG Pipeline.

Classifies question intent and handles metadata-only queries directly
from the database, bypassing the expensive RAG pipeline entirely.

Intent Types:
  COUNT          → "How many documents?"           → DB count, no RAG
  CLASSIFICATION → "Which category/domain?"        → metadata lookup, no RAG
  EXISTENCE      → "Is there any X document?"      → metadata check, no RAG
  FACTUAL_CONTENT→ "What platform is in scope?"    → Full RAG (Steps 1-4)
  HYBRID         → "What category and what about?" → metadata + short RAG
  CONVERSATIONAL → "Hi", "Thanks"                  → friendly response, no RAG

Attribute Types (HARD ROUTING):
  METADATA_ONLY     → Use database queries ONLY. NEVER use embeddings or RAG.
  DOCUMENT_EXIST    → Query document registry. NEVER use RAG.
  DOCUMENT_COUNT    → Query document registry. NEVER use embeddings.
  DOCUMENT_CATEGORY → Query category/domain registry. NEVER use RAG.
  DOCUMENT_REFERENCE→ Find document name first, optional chunks after.
  FACTUAL           → Full RAG pipeline required.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.sqlite.models import Document, Category, Domain, CategoryDomain


# =====================================================================
# INTENT ENUM
# =====================================================================
class QuestionIntent(str, Enum):
    COUNT = "count"
    CLASSIFICATION = "classification"
    EXISTENCE = "existence"
    DOCUMENT_LISTING = "document_listing"
    DOMAIN_QUERY = "domain_query"
    FACTUAL_CONTENT = "factual_content"
    HYBRID = "hybrid"
    CONVERSATIONAL = "conversational"


# =====================================================================
# ATTRIBUTE ENUM (HARD ROUTING CONSTRAINTS)
# =====================================================================
class QuestionAttribute(str, Enum):
    """
    Attribute is a HARD ROUTING CONSTRAINT.
    Determines which subsystem(s) can answer the question.
    """
    METADATA_ONLY = "metadata_only"      # NEVER use RAG
    DOCUMENT_EXIST = "document_exist"    # Check document titles/names
    DOCUMENT_COUNT = "document_count"    # Count from metadata
    DOCUMENT_CATEGORY = "document_category"  # Check category/domain registry
    DOCUMENT_REFERENCE = "document_reference"  # Find document, then optional chunks
    DOCUMENT_LISTING = "document_listing"
    DOMAIN_QUERY = "domain_query"
    FACTUAL = "factual"                  # Full RAG pipeline


# =====================================================================
# INTENT CLASSIFIER (rule-based, zero cost, instant)
# =====================================================================
def classify_intent(question: str) -> tuple[QuestionIntent, dict[str, Any]]:
    """
    Rule-based intent classification.  No API call, < 1 ms.
    Returns (intent, hints) where *hints* contains extracted metadata
    like domain_hint, category_hint, or doc_type for the handlers.
    
    CRITICAL FIX: Enhanced pattern matching to catch edge cases
    - "is there any" → "is there" (catch "is there a", "is there the", etc.)
    - Better keyword coverage
    """
    q = (question or "").lower().strip()
    words = q.split()

    # --- Sales intent detection (adds hints: sales_intent, buying_stage) ---
    sales_hints: dict[str, Any] = {}
    sales_patterns = {
        "Discovery": ["problem", "issue", "pain", "we have bugs", "seeing bugs", "struggling", "where to start", "diagnose", "assess"],
        "Solutioning": ["how to", "how do i", "implement", "automate", "set up", "integrate", "fix", "deploy", "automation", "ci/cd", "test", "stabilize", "improve", "optimize"],
        "Objection": ["too expensive", "cost", "pricey", "budget", "afford", "not worth", "concern", "hesitant", "risk"],
        "Proof": ["case study", "have you worked", "who have you", "references", "clients", "proof", "evidence", "experience"],
        "Decision": ["buy", "purchase", "pricing", "proposal", "contract", "partner", "ready to buy", "sign", "engage", "trial"]
    }

    for intent_name, kws in sales_patterns.items():
        for kw in kws:
            if kw in q:
                stage = "Top"
                if intent_name in ("Solutioning", "Objection", "Proof"):
                    stage = "Middle"
                if intent_name == "Decision":
                    stage = "Bottom"
                sales_hints["sales_intent"] = intent_name
                sales_hints["buying_stage"] = stage
                # capture first matching sales intent only
                break
        if sales_hints:
            break

    # ---- CONVERSATIONAL ----
    greetings = {
        "hi", "hello", "hey", "good morning", "good afternoon",
        "good evening", "good night", "thanks", "thank you",
        "bye", "goodbye", "see you", "ok", "okay", "sure", "great",
    }
    # Consider short greetings and sentences that start with a greeting
    if q in greetings or len(words) <= 1 or any(q.startswith(g) for g in greetings):
        return QuestionIntent.CONVERSATIONAL, {**{}, **sales_hints}

    # ---- FACTUAL CONTENT ----
    factual_triggers = [
        "percentage", "modules", "roi", "cost", "revenue", "profit",
        "efficiency", "metrics", "analysis", "performance"
    ]
    if any(t in q for t in factual_triggers):
        return QuestionIntent.FACTUAL_CONTENT, {**{}, **sales_hints}

    # ---- COUNT ----
    count_triggers = [
        "how many", "count of", "number of",
        "total number", "total count", "how much",
        "total", "altogether",
    ]
    if any(t in q for t in count_triggers):
        hints = _extract_count_hints(q)
        return QuestionIntent.COUNT, {**hints, **sales_hints}

    # ---- EXISTENCE ---- (ENHANCED: More inclusive patterns)
    # Key fixes:
    # - Changed "is there any" to "is there" (catches "is there a", "is there the")
    # - Added "do we have", "do you have" without "any"
    # - Added "are there", "have we", "got"
    existence_triggers = [
        r"is there",              # FIXED: "is there a" now matches
        r"do we have",            # FIXED: more inclusive
        r"do you have",           # FIXED: more inclusive
        r"are there",             # More variants
        r"have we",               # More variants
        r"have you",              # More variants
        r"do you exist",
        r"does .+ exist",
        r"have any",
        r"got any",
        r"any.*document",         # "any X document"
        r"any.*pdf",              # "any X PDF"
        r"available",             # "are X available?"
    ]
    if any(re.search(t, q) for t in existence_triggers):
        hints = _extract_existence_hints(q)
        return QuestionIntent.EXISTENCE, {**hints, **sales_hints}

    # ---- CLASSIFICATION ---- (ENHANCED: Document category/domain lookup)
    classification_triggers = [
        "which category", "under what category", "under which category",
        "what category", "which domain", "under what domain",
        "under which domain", "what domain", "which collection",
        "what collection", "belongs to which", "belong to which",
        "categorized under", "classified under", "fall under",
        "comes under", "come under", "grouped under",
        "associated with which", "assigned to which",
    ]
    if any(t in q for t in classification_triggers):
        hints = _extract_classification_hints(q)
        return QuestionIntent.CLASSIFICATION, {**hints, **sales_hints}

    # "Which documents belong to X" / "Which documents are in X" pattern
    # Check this before other classification patterns to prioritize listing queries
    if re.search(r"which\s+documents?\b", q):
        hints = _extract_classification_hints(q)
        return QuestionIntent.DOCUMENT_LISTING, {**hints, **sales_hints}

    # Domain-specific queries (e.g., "related to cybersecurity", "in the X domain")
    if re.search(r"related to\s+|\bin\s+the\s+.+\s+domain\b|\bwhat domain\b|\bwhich domain\b", q):
        hints = _extract_classification_hints(q)
        return QuestionIntent.DOMAIN_QUERY, {**hints, **sales_hints}

    # ---- HYBRID ----
    if re.search(
        r"(?:which|what)\s+(?:category|domain|collection).+and.+(?:about|what|why|tell)",
        q,
    ):
        return QuestionIntent.HYBRID, {**{}, **sales_hints}

    # ---- DEFAULT ----
    return QuestionIntent.FACTUAL_CONTENT, {**{}, **sales_hints}


# =====================================================================
# SOLUTION PRIORITIZER
# =====================================================================
SOLUTION_KEYWORDS: dict[str, list[str]] = {
    "BugBuster": [
        "production bug", "production bugs", "hotfix", "stabilize", "stability",
        "crash", "urgent", "emergency", "customer complaint", "customer complaints",
        "critical defect",
    ],
    "Fastrack Automation": [
        "flaky test", "flaky tests", "flaky", "automation", "ci/cd", "regression time",
        "slow release", "speed up", "pipeline", "ci", "cd", "test suite",
    ],
    "QA Ownership": [
        "hiring", "team size", "resource crunch", "manage qa", "qa ownership",
        "outsourcing", "take ownership", "manage QA", "scale team",
    ],
    "MoolyAImpact": [
        "process", "audit", "strategy", "transformation", "ai adoption",
        "what should we do", "consulting", "process chaos", "modernize", "strategy advice",
    ],
}


def recommend_solution(question: str) -> str | None:
    """Heuristic mapping from question text to a target solution name.

    Returns one of the keys in `SOLUTION_KEYWORDS` or None if no match.
    """
    q = (question or "").lower()
    for solution, keywords in SOLUTION_KEYWORDS.items():
        for kw in keywords:
            if kw in q:
                return solution
    return None


def handle_objection(question: str) -> str | None:
    """Simple rule-based objection handler.

    Detects common objection types (price, competition, DIY) and returns
    a concise Sales Mode response following Recommendation → Why → How → Proof.
    Returns None if it cannot confidently handle the objection.
    """
    q = (question or "").lower()

    price_kw = ["too expensive", "expensive", "cost", "pricey", "budget", "afford"]
    comp_kw = ["competitor", "vs ", "instead of", "better than", "compare", "comparative"]
    diy_kw = ["do it ourselves", "in-house", "build ourselves", "diy", "internal team"]

    # Price objection
    if any(kw in q for kw in price_kw):
        return (
            "Recommendation: We can discuss flexible pricing or a pilot to reduce upfront cost.\n"
            "Why: A short pilot reduces your risk and demonstrates value before larger spend.\n"
            "How: Start with a 2-4 week stabilization pilot (scope: critical flows), deliver quick wins, then expand.\n"
            "Proof: I can pull case studies showing reduced time-to-stability and cost benefits — would you like me to fetch them?"
        )

    # Competition comparison
    if any(kw in q for kw in comp_kw):
        return (
            "Recommendation: Let's evaluate the key decision criteria (TCO, time-to-value, support).\n"
            "Why: Feature parity is just one axis — deployment speed and support often determine success.\n"
            "How: I can produce a short comparison matrix vs typical competitors and highlight differentiators.\n"
            "Proof: I can fetch examples and outcomes from our prior engagements — shall I pull those now?"
        )

    # DIY / build vs buy
    if any(kw in q for kw in diy_kw):
        return (
            "Recommendation: Consider a phased approach — pilot with our team, then transition knowledge to your team.\n"
            "Why: Building in-house often delays time-to-value and increases hidden costs.\n"
            "How: Start with our rapid-assist engagement (knowledge transfer included) to accelerate delivery and lower risk.\n"
            "Proof: I can pull examples where clients adopted this model to scale efficiently — would you like those?"
        )

    return None


# =====================================================================
# ATTRIBUTE MAPPER (Maps Intent → Attribute for hard routing)
# =====================================================================
def map_intent_to_attribute(intent: QuestionIntent) -> QuestionAttribute:
    """
    Map Intent to Attribute for HARD ROUTING CONSTRAINTS.
    
    This ensures metadata-only questions NEVER reach RAG,
    and RAG questions NEVER try to answer metadata queries.
    """
    mapping = {
        QuestionIntent.CONVERSATIONAL: QuestionAttribute.METADATA_ONLY,
        QuestionIntent.COUNT: QuestionAttribute.DOCUMENT_COUNT,
        QuestionIntent.EXISTENCE: QuestionAttribute.DOCUMENT_EXIST,
        QuestionIntent.CLASSIFICATION: QuestionAttribute.DOCUMENT_CATEGORY,
        QuestionIntent.DOCUMENT_LISTING: QuestionAttribute.DOCUMENT_LISTING,
        QuestionIntent.DOMAIN_QUERY: QuestionAttribute.DOMAIN_QUERY,
        QuestionIntent.HYBRID: QuestionAttribute.DOCUMENT_REFERENCE,
        QuestionIntent.FACTUAL_CONTENT: QuestionAttribute.FACTUAL,
    }
    return mapping.get(intent, QuestionAttribute.FACTUAL)


# =====================================================================
# HINT EXTRACTION HELPERS
# =====================================================================
def _extract_count_hints(q: str) -> dict[str, Any]:
    """Pull filtering clues from count questions."""
    hints: dict[str, Any] = {}

    # Type hints
    type_map = {
        "proposal": "proposal", "proposals": "proposal",
        "case study": "case_study", "case studies": "case_study",
        "solution": "solution", "solutions": "solution",
        "service": "solution", "services": "solution",
        "policy": "policy", "policies": "policy",
    }
    for kw, doc_type in type_map.items():
        if kw in q:
            hints["doc_type"] = doc_type
            break

    # Domain hint  ("under AI engineering domain")
    m = re.search(r"(?:under|in|from|of)\s+(?:the\s+)?(.+?)\s+domain", q)
    if m:
        hints["domain_hint"] = m.group(1).strip()

    # Category hint  ("in proposals category")
    m = re.search(r"(?:under|in|from|of)\s+(?:the\s+)?(.+?)\s+(?:category|collection)", q)
    if m:
        hints["category_hint"] = m.group(1).strip()

    return hints


def _extract_existence_hints(q: str) -> dict[str, Any]:
    """
    Extract hints for existence checking.
    Examples: "Is there a cybersecurity policy PDF?"
              "Do we have any HR documents?"
    """
    hints: dict[str, Any] = {}
    
    # Extract document type/keyword they're looking for
    type_map = {
        "proposal": "proposal", "proposals": "proposal",
        "case study": "case_study", "case studies": "case_study",
        "solution": "solution", "solutions": "solution",
        "service": "solution", "services": "solution",
        "policy": "policy", "policies": "policy",
        "pdf": "pdf",
        "document": "document",
    }
    for kw, doc_type in type_map.items():
        if kw in q:
            hints["search_type"] = doc_type
            break
    
    return hints


def _extract_classification_hints(q: str) -> dict[str, Any]:
    """Extract hints for classification queries."""
    hints: dict[str, Any] = {}
    
    # Look for category/domain being asked about
    m = re.search(r"(?:category|collection)\s+(?:is|of)?\s+(.+?)(?:\?|$)", q)
    if m:
        hints["target_category"] = m.group(1).strip()
    
    m = re.search(r"domain\s+(?:is|of)?\s+(.+?)(?:\?|$)", q)
    if m:
        hints["target_domain"] = m.group(1).strip()
    
    return hints


# =====================================================================
# METADATA HANDLERS  (no RAG, direct DB queries)
# =====================================================================

def handle_count(
    question: str,
    db: Session,
    categories: list[Category],
    hints: dict[str, Any],
) -> str:
    """Handle COUNT intent: answer with document counts from the database."""
    q = question.lower()

    # --- Filter by domain ---
    domain_hint = hints.get("domain_hint")
    if domain_hint:
        domain = db.query(Domain).filter(
            Domain.name.ilike(f"%{domain_hint}%")
        ).first()

        if domain:
            cat_ids = [
                cd.category_id
                for cd in db.query(CategoryDomain).filter(
                    CategoryDomain.domain_id == domain.id
                ).all()
            ]
            if cat_ids:
                count = db.query(Document).filter(
                    Document.category_id.in_(cat_ids),
                    Document.processed == True,
                ).count()
                return (
                    f"There are **{count} document{'s' if count != 1 else ''}** "
                    f"under the **{domain.name}** domain."
                )
            return f"The **{domain.name}** domain exists but has no categories assigned yet."
        return f"I couldn't find a domain matching '{domain_hint}'."

    # --- Filter by category ---
    category_hint = hints.get("category_hint")
    if category_hint:
        for cat in categories:
            if (
                category_hint in cat.name.lower()
                or category_hint in cat.collection_name.lower()
            ):
                count = db.query(Document).filter(
                    Document.category_id == cat.id,
                    Document.processed == True,
                ).count()
                return (
                    f"There are **{count} document{'s' if count != 1 else ''}** "
                    f"in the **{cat.name}** collection."
                )
        return f"I couldn't find a category matching '{category_hint}'."

    # --- Filter by doc type keyword ---
    doc_type = hints.get("doc_type")
    if doc_type:
        kw_map = {
            "proposal": ["proposal"],
            "case_study": ["case stud", "case_stud"],
            "solution": ["solution", "service"],
            "policy": ["policy", "policies"],
        }
        keywords = kw_map.get(doc_type, [doc_type])
        matching = [
            cat for cat in categories
            if any(
                kw in cat.name.lower() or kw in cat.collection_name.lower()
                for kw in keywords
            )
        ]
        if matching:
            total = 0
            for cat in matching:
                total += db.query(Document).filter(
                    Document.category_id == cat.id,
                    Document.processed == True,
                ).count()
            label = doc_type.replace("_", " ")
            return (
                f"There are **{total} {label} document{'s' if total != 1 else ''}** "
                f"in the **{matching[0].name}** collection."
            )

    # --- Generic count (all documents) ---
    total = db.query(Document).filter(Document.processed == True).count()
    breakdown = []
    for cat in categories:
        c = db.query(Document).filter(
            Document.category_id == cat.id,
            Document.processed == True,
        ).count()
        if c > 0:
            breakdown.append(f"• **{cat.name}**: {c}")

    answer = f"There are **{total} document{'s' if total != 1 else ''}** in total."
    if breakdown:
        answer += "\n\n" + "\n".join(breakdown)
    return answer


def handle_classification(
    question: str,
    db: Session,
    categories: list[Category],
    entity: str | None,
) -> str | None:
    """
    Handle CLASSIFICATION intent.
    Returns formatted answer or *None* if it cannot resolve from metadata
    (caller should fall through to RAG in that case).
    """
    q = question.lower()

    # If we have an entity, look it up in documents
    if entity:
        docs = db.query(Document).filter(
            Document.title.ilike(f"%{entity}%"),
            Document.processed == True,
        ).all()

        if docs:
            results = []
            for doc in docs:
                cat_name = "Uncategorized"
                domain_names: list[str] = []

                if doc.category_id:
                    cat = db.query(Category).filter(Category.id == doc.category_id).first()
                    if cat:
                        cat_name = cat.name
                        d_ids = [
                            cd.domain_id
                            for cd in db.query(CategoryDomain).filter(
                                CategoryDomain.category_id == cat.id
                            ).all()
                        ]
                        if d_ids:
                            domain_names = [
                                d.name for d in db.query(Domain).filter(Domain.id.in_(d_ids)).all()
                            ]
                elif doc.category:
                    cat_name = doc.category

                line = f"**{doc.title}** → Category: **{cat_name}**"
                if domain_names:
                    line += f" | Domain: **{', '.join(domain_names)}**"
                results.append(line)

            if len(docs) == 1:
                return results[0]
            return f"Found {len(docs)} documents matching '{entity}':\n\n" + "\n".join(
                f"• {r}" for r in results
            )
        return f"I couldn't find any documents with '{entity}' in the title."

    # "Which documents belong to X category/collection?"
    for cat in categories:
        if cat.name.lower() in q or cat.collection_name.lower() in q:
            docs = (
                db.query(Document)
                .filter(Document.category_id == cat.id, Document.processed == True)
                .order_by(Document.title)
                .all()
            )
            if docs:
                titles = [f"• {d.title}" for d in docs]
                return (
                    f"The **{cat.name}** collection has "
                    f"**{len(docs)} document{'s' if len(docs) != 1 else ''}**:\n\n"
                    + "\n".join(titles)
                )
            return f"The **{cat.name}** collection exists but has no documents yet."

    return None  # Couldn't resolve from metadata → fall through to RAG


def handle_listing(
    question: str,
    db: Session,
    categories: list[Category],
    hints: dict[str, Any],
) -> str:
    """Handle DOCUMENT_LISTING: return names of documents matching filters.

    Examples:
    - "Which documents contain multiple case studies?"
    - "List documents in the proposals collection"
    """
    q = question.lower()

    # Filter by category hint
    category_hint = hints.get("category_hint") or hints.get("target_category")
    if category_hint:
        for cat in categories:
            if category_hint in cat.name.lower() or category_hint in cat.collection_name.lower():
                docs = (
                    db.query(Document)
                    .filter(Document.category_id == cat.id, Document.processed == True)
                    .order_by(Document.title)
                    .all()
                )
                if docs:
                    titles = [f"• {d.title}" for d in docs]
                    return (
                        f"The **{cat.name}** collection has **{len(docs)} document{'s' if len(docs) != 1 else ''}**:\n\n"
                        + "\n".join(titles)
                    )
                return f"The **{cat.name}** collection exists but has no documents yet."

    # Filter by domain hint
    domain_hint = hints.get("domain_hint")
    if domain_hint:
        dom = db.query(Domain).filter(Domain.name.ilike(f"%{domain_hint}%")).first()
        if dom:
            cat_ids = [cd.category_id for cd in db.query(CategoryDomain).filter(CategoryDomain.domain_id == dom.id).all()]
            if cat_ids:
                docs = db.query(Document).filter(Document.category_id.in_(cat_ids), Document.processed == True).order_by(Document.title).all()
                if docs:
                    titles = [f"• {d.title}" for d in docs]
                    return f"Found **{len(docs)} documents** under the **{dom.name}** domain:\n\n" + "\n".join(titles)
                return f"The **{dom.name}** domain exists but has no documents yet."
        return f"I couldn't find a domain matching '{domain_hint}'."

    # Generic listing: return recent/top documents
    docs = db.query(Document).filter(Document.processed == True).order_by(Document.created_at.desc()).limit(20).all()
    if docs:
        titles = [f"• {d.title}" for d in docs]
        return "Here are some documents I have:\n\n" + "\n".join(titles)
    return "I don't have any documents in the registry yet."


def handle_domain_query(
    question: str,
    db: Session,
    categories: list[Category],
    hints: dict[str, Any] | None = None,
) -> str:
    """Handle DOMAIN_QUERY: answer using domain, tags, and service taxonomy.

    Examples:
    - "Is there doc related to cybersecurity domain?"
    - "Which documents are related to healthcare?"
    """
    if hints is None:
        hints = {}
    q = question.lower()

    # Try to extract domain hint from hints or question
    domain_hint = hints.get("target_domain") or hints.get("domain_hint")
    if not domain_hint:
        m = re.search(r"related to\s+(.+?)(?:\?|$)", q)
        if m:
            domain_hint = m.group(1).strip()

    if domain_hint:
        dom = db.query(Domain).filter(Domain.name.ilike(f"%{domain_hint}%")).first()
        if dom:
            # List categories and counts under this domain
            cd_rows = db.query(CategoryDomain).filter(CategoryDomain.domain_id == dom.id).all()
            if not cd_rows:
                return f"The **{dom.name}** domain exists but is not associated with any collections."
            cats = db.query(Category).filter(Category.id.in_([c.category_id for c in cd_rows])).all()
            lines = []
            total = 0
            for c in cats:
                ccount = db.query(Document).filter(Document.category_id == c.id, Document.processed == True).count()
                total += ccount
                lines.append(f"• **{c.name}**: {ccount}")
            return f"The **{dom.name}** domain has **{total} document{'s' if total != 1 else ''}** across these collections:\n\n" + "\n".join(lines)
        return f"I couldn't find a domain matching '{domain_hint}'."

    return "Could you clarify which domain or topic you're asking about?"


def handle_existence(
    question: str,
    db: Session,
    categories: list[Category],
    entity: str | None,
    hints: dict[str, Any] | None = None,
) -> str:
    """
    Handle EXISTENCE intent: does a document / category exist?
    
    ENHANCED: Check document titles, descriptions, and keywords.
    Examples:
    - "Is there a cybersecurity policy PDF uploaded?"
    - "Do we have any HR documents?"
    - "Do we have a Benow proposal?"
    """
    if hints is None:
        hints = {}
    
    q = question.lower()

    # ---- Check by entity (document name) ----
    if entity:
        docs = db.query(Document).filter(
            Document.title.ilike(f"%{entity}%"),
            Document.processed == True,
        ).all()

        if docs:
            titles = [f"• {d.title}" for d in docs[:5]]
            plural = len(docs) != 1
            answer = (
                f"Yes, there {'are' if plural else 'is'} "
                f"**{len(docs)} document{'s' if plural else ''}** "
                f"related to **{entity}**:\n\n" + "\n".join(titles)
            )
            if len(docs) > 5:
                answer += f"\n\n...and {len(docs) - 5} more."
            return answer
        # Don't return "not found" yet - check by type keyword below

    # ---- Check by search type keyword (proposal, policy, etc.) ----
    search_type = hints.get("search_type", "").lower()
    if search_type and search_type != "document":
        # Build keywords to search for
        type_kw_map = {
            "proposal": ["proposal"],
            "case_study": ["case stud", "case_stud"],
            "case studies": ["case stud", "case_stud"],
            "solution": ["solution"],
            "service": ["service"],
            "policy": ["policy", "policies"],
            "pdf": ["pdf"],
        }
        search_keywords = type_kw_map.get(search_type, [search_type])
        
        # Search in document titles and descriptions
        matching_docs = db.query(Document).filter(
            Document.processed == True
        ).all()
        
        found = []
        for doc in matching_docs:
            title_match = any(kw in doc.title.lower() for kw in search_keywords)
            desc_match = (
                doc.description and 
                any(kw in doc.description.lower() for kw in search_keywords)
            )
            if title_match or desc_match:
                found.append(doc)
        
        if found:
            titles = [f"• {d.title}" for d in found[:5]]
            label = search_type.replace("_", " ").replace(" stud", " study")
            plural = len(found) != 1
            answer = (
                f"Yes, there {'are' if plural else 'is'} "
                f"**{len(found)} {label} document{'s' if plural else ''}**:\n\n" + 
                "\n".join(titles)
            )
            if len(found) > 5:
                answer += f"\n\n...and {len(found) - 5} more."
            return answer

    # ---- Check by category/type keywords in question ----
    type_kw = {
        "proposal": ["proposal"],
        "case study": ["case stud", "case_stud"],
        "case studies": ["case stud", "case_stud"],
        "solution": ["solution"],
        "service": ["service"],
        "policy": ["policy", "policies"],
    }
    for type_name, keywords in type_kw.items():
        if type_name in q:
            cats = [
                c for c in categories
                if any(
                    kw in c.name.lower() or kw in c.collection_name.lower()
                    for kw in keywords
                )
            ]
            if cats:
                total = sum(
                    db.query(Document)
                    .filter(Document.category_id == c.id, Document.processed == True)
                    .count()
                    for c in cats
                )
                if total > 0:
                    return (
                        f"Yes, there {'are' if total != 1 else 'is'} "
                        f"**{total} {type_name} document{'s' if total != 1 else ''}**."
                    )
                return f"The {type_name} category exists but has no documents yet."
            return f"No, I don't have a category for {type_name} documents."

    # ---- Generic: Check if any documents exist ----
    total = db.query(Document).filter(Document.processed == True).count()
    if total > 0:
        return f"Yes, I have **{total} document{'s' if total != 1 else ''}** across all collections."
    else:
        return "No, I don't have any documents in my registry yet."


def handle_conversational(question: str) -> str:
    """Handle greetings and conversational messages."""
    q = question.lower().strip()

    if q in {"thanks", "thank you"}:
        return "You're welcome! Feel free to ask if you have more questions. 😊"
    if q in {"bye", "goodbye", "see you"}:
        return "Goodbye! Have a great day! 👋"
    if q in {"ok", "okay", "sure", "great"}:
        return "Got it! Let me know if you need anything else."

    return (
        "Hello! 👋 I'm your document assistant. I can help you with:\n\n"
        "• **Finding information** in uploaded documents\n"
        "• **Counting** documents by category or domain\n"
        "• **Checking** which documents are available\n\n"
        "What would you like to know?"
    )
