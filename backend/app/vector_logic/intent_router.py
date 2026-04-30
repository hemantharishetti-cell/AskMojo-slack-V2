"""
Step 0: Intent-Based Router for the RAG Pipeline.

Classifies question intent and handles metadata-only queries directly
from the database, bypassing the expensive RAG pipeline entirely.

Intent Types:
  COUNT           -> "How many documents?"            -> DB count, no RAG
  CLASSIFICATION  -> "Which category/domain?"         -> metadata lookup, no RAG
  EXISTENCE       -> "Is there any X document?"       -> metadata check, no RAG
  DOCUMENT_LISTING -> "List documents"                -> metadata list, no RAG
  DOMAIN_QUERY    -> "What domains do we have?"       -> registry query, no RAG
  FACTUAL_CONTENT -> "What platform is in scope?"     -> Full RAG (Steps 1-4)
  HYBRID          -> "What category and what about?"  -> metadata + short RAG
  CONVERSATIONAL  -> "Hi", "Thanks"                   -> friendly response, no RAG
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any

from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session

from app.sqlite.models import Document, Category, Domain, CategoryDomain

# ---------------------------------------------------------------------------
# Backward-compat re-exports
# routes.py imports these names from intent_router; keep them available here
# so that import chain stays intact without touching routes.py.
# ---------------------------------------------------------------------------
from app.schemas.intent import QuestionAttribute  # noqa: F401  (re-export)

# Keywords still referenced by routes.py
SOLUTION_KEYWORDS: frozenset[str] = frozenset({
    "bugbuster", "codeprobe", "fastrack", "continuouscare", "moolyaimpact",
    "solution", "solutions", "tool", "tools", "product", "products",
})


def map_intent_to_attribute(intent: "QuestionIntent") -> "QuestionAttribute":
    """Map a QuestionIntent to its corresponding QuestionAttribute.

    Kept for backward compatibility with routes.py callers.
    """
    from app.schemas.intent import QuestionAttribute as QA
    mapping = {
        "count": QA.DOCUMENT_COUNT,
        "existence": QA.DOCUMENT_EXIST,
        "document_listing": QA.DOCUMENT_LISTING,
        "domain_query": QA.DOMAIN_QUERY,
        "classification": QA.DOCUMENT_CATEGORY,
        "conversational": QA.METADATA_ONLY,
    }
    intent_val = getattr(intent, "value", str(intent))
    return mapping.get(intent_val, QA.FACTUAL)


def recommend_solution(question: str, chunks: list | None = None) -> str | None:
    """Return the name of the best-matching solution product, or None.

    Kept for backward compatibility with routes.py callers.
    Simple keyword match â€” routes.py only checks truthiness of the return value.
    """
    q = (question or "").lower()
    for kw in SOLUTION_KEYWORDS:
        if kw in q:
            return kw
    return None


def handle_objection(question: str, db: Session | None = None, **kwargs) -> str | None:
    """Handle objection-style questions.

    Kept for backward compatibility with routes.py callers.
    Returns None so that routes.py falls through to its normal RAG path.
    """
    return None


# =====================================================================
# TEXT NORMALISATION
# =====================================================================

def _normalize_registry_text(text: str) -> str:
    """Normalize text for deterministic exact registry matching."""
    t = (text or "").lower()
    t = t.replace("_", " ").replace("-", " ")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _resolve_domain_from_registry(
    db: Session,
    question: str,
    hints: dict[str, Any] | None = None,
) -> Domain | None:
    """Resolve a domain strictly by exact match against the Domain registry.

    Matching rules:
    - Case-insensitive
    - Minor whitespace / '_' / '-' variations tolerated
    - Exact domain name match (normalized) either from extracted hint or phrase match in question
    """
    h = hints or {}
    candidate = h.get("domain") or h.get("target_domain") or h.get("domain_hint")

    q_norm = f" {_normalize_registry_text(question)} "
    domains = db.query(Domain).all()
    norm_map: dict[str, Domain] = {
        _normalize_registry_text(d.name): d for d in domains if d.name
    }

    if candidate:
        cand_norm = _normalize_registry_text(candidate)
        if cand_norm in norm_map:
            return norm_map[cand_norm]

    # Phrase match: find a full normalized domain token inside the normalized question
    for dn_norm, dom in norm_map.items():
        if dn_norm and f" {dn_norm} " in q_norm:
            return dom

    return None


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
# SHARED UTILITIES
# =====================================================================

def _domain_not_found_response(hints: dict) -> str:
    """Return a deterministic 'domain not found' message from hints."""
    raw = hints.get("domain") or hints.get("domain_hint") or hints.get("target_domain")
    return (
        f"Domain '{raw}' is not found in our system."
        if raw
        else "This domain is not listed in our registry."
    )


def _is_domain_scoped(q: str, hints: dict, dom: Domain | None) -> bool:
    """Return True when the question is clearly scoped to a specific domain."""
    return bool(
        ("domain" in q)
        or hints.get("domain")
        or hints.get("domain_hint")
        or hints.get("target_domain")
        or dom is not None
    )


# =====================================================================
# INTENT CLASSIFIER (rule-based, zero cost, instant)
# =====================================================================

def classify_intent_light(question: str) -> dict[str, str]:
    """Lightweight, zero-LLM intent classification for unambiguous queries."""
    q = (question or "").lower().strip()
    q_norm = re.sub(r"[^a-z0-9\s]", " ", q)
    q_norm = re.sub(r"\s+", " ", q_norm).strip()

    if not q:
        return {"route": "RAG"}

    count_terms = re.search(r"\b(how many|count|number of|total)\b", q_norm)
    registry_terms = re.search(
        r"\b(documents?|docs?|files?|domains?|collections?|categories?|items?|entries?|records?|proposals?|cases?\s+stud(?:y|ies)|solutions?|services?|policies?)\b",
        q_norm,
    )
    time_units = re.search(r"\b(years?|months?|weeks?|days?|hours?|minutes?|seconds?)\b", q_norm)
    possessive_doc_context = re.search(r"\b(do you have|we have|in total|overall)\b", q_norm)

    if count_terms:
        # Keep timeline-style questions in RAG unless they clearly target registry objects.
        if time_units and not registry_terms:
            return {"route": "RAG"}
        if registry_terms or possessive_doc_context:
            return {"route": "METADATA", "type": "COUNT"}
        return {"route": "RAG"}

    if re.search(r"\b(list|show)\b", q_norm) and re.search(
        r"\b(documents?|docs?|files?|domains?|collections?|categories?)\b", q_norm
    ):
        return {"route": "METADATA", "type": "LIST"}
    if re.search(r"\b(which|what)\s+(documents?|docs?|files?)\b", q_norm):
        return {"route": "METADATA", "type": "LIST"}

    if re.search(r"\b(is there|do we have|do you have|do u have|exists|are there)\b", q_norm):
        return {"route": "METADATA", "type": "EXISTENCE"}

    conversational_patterns = [
        r"^(?:hi|hii+|hello|hey)(?:\s+mojo)?$",
        r"^(?:bye|goodbye|see you)(?:\s+mojo)?$",
        r"^(?:thanks|thank you)(?:\s+mojo)?$",
        r"^how are (?:you|things)(?:\s+mojo)?$",
        r"^how s it going(?:\s+mojo)?$",
    ]
    if len(q_norm.split()) <= 5 and any(re.match(pattern, q_norm) for pattern in conversational_patterns):
        return {"route": "METADATA", "type": "CONVERSATIONAL"}

    return {"route": "RAG"}


def classify_intent(question: str) -> tuple[QuestionIntent, dict]:
    """
    Classify question intent.

    Fast path: unambiguous structural queries -> instant regex match (< 1ms)
    Slow path: all other queries -> returns FACTUAL_CONTENT for RAG pipeline.
    """
    route_info = classify_intent_light(question)
    if route_info.get("route") != "METADATA":
        return QuestionIntent.FACTUAL_CONTENT, {}

    _type_map = {
        "COUNT":        QuestionIntent.COUNT,
        "LIST":         QuestionIntent.DOCUMENT_LISTING,
        "EXISTENCE":    QuestionIntent.EXISTENCE,
        "CONVERSATIONAL": QuestionIntent.CONVERSATIONAL,
    }
    t = (route_info.get("type") or "").upper()
    return _type_map.get(t, QuestionIntent.FACTUAL_CONTENT), {}


# =====================================================================
# HINT EXTRACTION HELPERS
# =====================================================================

def _extract_count_hints(q: str) -> dict[str, Any]:
    """Pull filtering clues from count questions."""
    hints: dict[str, Any] = {}

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

    m = re.search(r"(?:under|in|from|of)\s+(?:the\s+)?(.+?)\s+domain", q)
    if m:
        hints["domain_hint"] = m.group(1).strip()

    m = re.search(r"(?:under|in|from|of)\s+(?:the\s+)?(.+?)\s+(?:category|collection)", q)
    if m:
        hints["category_hint"] = m.group(1).strip()

    return hints


def _extract_doc_hint_from_query(q: str) -> str | None:
    """Extract a specific document hint from natural-language existence queries."""
    text = (q or "").strip()
    if not text:
        return None

    quoted = re.search(r"[\"']([^\"']{3,})[\"']", text)
    if quoted:
        return quoted.group(1).strip()

    patterns = [
        r"\bdo\s+(?:you|u|we)\s+have\s+(?:a|an|the)?\s*(.+?)(?:\?|$)",
        r"\bis\s+there\s+(?:a|an|the)?\s*(.+?)(?:\?|$)",
        r"\bdoes\s+(.+?)\s+exist(?:\s+in\s+the\s+system)?(?:\?|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        candidate = (m.group(1) or "").strip()
        candidate = re.sub(r"\b(?:document|documents|doc|docs|file|files|pdf|uploaded)\b$", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(r"^(?:any|some)\s+", "", candidate, flags=re.IGNORECASE).strip()
        if candidate and candidate not in {"this", "that", "it", "document", "doc"}:
            return candidate

    return None


def _extract_category_hint_from_query(q: str) -> str | None:
    """Extract explicit category/collection mention from listing/count queries."""
    text = (q or "").strip()
    if not text:
        return None

    patterns = [
        r"\b(?:under|in|from)\s+(?:the\s+)?(.+?)\s+(?:collection|category|domain)(?:\?|$)",
        r"\b(?:under|in|from)\s+(?:the\s+)?(.+?)(?:\?|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        candidate = (m.group(1) or "").strip()
        candidate = re.sub(r"\b(?:documents?|docs?|files?)\b$", "", candidate, flags=re.IGNORECASE).strip()
        if candidate:
            return candidate
    return None


def extract_metadata_hints(question: str, intent_type: str | None = None) -> dict[str, Any]:
    """
    Build metadata hints for SQL-only handlers.
    This is used by both the classic routes and the deep-agent fast path.
    """
    q = (question or "").lower().strip()
    t = (intent_type or "").upper().strip()

    hints: dict[str, Any] = {}
    hints.update(_extract_count_hints(q))
    hints.update(_extract_classification_hints(q))

    if t in {"EXISTENCE", "LIST", ""}:
        hints.update(_extract_existence_hints(q))

    doc_hint = _extract_doc_hint_from_query(question)
    if doc_hint:
        hints["doc_hint"] = doc_hint

    category_hint = _extract_category_hint_from_query(question)
    if category_hint:
        hints["category_hint"] = category_hint

    return hints


def _extract_existence_hints(q: str) -> dict[str, Any]:
    """
    Extract hints for existence checking.
    Examples: "Is there a cybersecurity policy PDF?"
              "Do we have any HR documents?"
    """
    hints: dict[str, Any] = {}
    type_map = {
        "proposal": "proposal", "proposals": "proposal",
        "case study": "case_study", "case studies": "case_study",
        "solution": "solution", "solutions": "solution",
        "service": "solution", "services": "solution",
        "policy": "policy", "policies": "policy",
        "pdf": "pdf", "document": "document",
    }
    for kw, doc_type in type_map.items():
        if kw in q:
            hints["search_type"] = doc_type
            break
    doc_hint = _extract_doc_hint_from_query(q)
    if doc_hint:
        hints["doc_hint"] = doc_hint
    return hints


def _extract_classification_hints(q: str) -> dict[str, Any]:
    """Extract hints for classification queries."""
    hints: dict[str, Any] = {}
    m = re.search(r"(?:category|collection)\s+(?:is|of)?\s+(.+?)(?:\?|$)", q)
    if m:
        hints["target_category"] = m.group(1).strip()
    m = re.search(r"domain\s+(?:is|of)?\s+(.+?)(?:\?|$)", q)
    if m:
        hints["target_domain"] = m.group(1).strip()
    return hints


def _normalize_match_key(text: str | None) -> str:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _match_category_by_hint(category_hint: str | None, categories: list[Category]) -> Category | None:
    hint = _normalize_match_key(category_hint)
    if not hint:
        return None

    best: Category | None = None
    best_score = -1
    for cat in categories or []:
        name = _normalize_match_key(getattr(cat, "name", ""))
        collection = _normalize_match_key(getattr(cat, "collection_name", ""))
        score = -1
        if hint == name or hint == collection:
            score = 100 + len(hint)
        elif hint and (hint in name or name in hint):
            score = 50 + min(len(hint), len(name))
        elif hint and (hint in collection or collection in hint):
            score = 50 + min(len(hint), len(collection))
        if score > best_score:
            best_score = score
            best = cat
    return best if best_score >= 0 else None


def _resolve_category_from_question_or_hints(
    question: str,
    categories: list[Category],
    hints: dict[str, Any] | None = None,
) -> tuple[Category | None, str | None]:
    h = hints or {}
    explicit_hint = h.get("category_hint") or h.get("target_category")
    if explicit_hint:
        return _match_category_by_hint(str(explicit_hint), categories), str(explicit_hint)

    query_hint = _extract_category_hint_from_query(question)
    if query_hint:
        return _match_category_by_hint(query_hint, categories), query_hint

    q_norm = _normalize_match_key(question)
    best: Category | None = None
    best_len = -1
    for cat in categories or []:
        name = _normalize_match_key(getattr(cat, "name", ""))
        collection = _normalize_match_key(getattr(cat, "collection_name", ""))
        for candidate in (name, collection):
            if candidate and candidate in q_norm and len(candidate) > best_len:
                best = cat
                best_len = len(candidate)
    return best, None


def _find_docs_by_title_hint(db: Session, title_hint: str) -> list[Document]:
    """Find documents by title hint using exact ilike first, then normalized fallback."""
    hint = (title_hint or "").strip()
    if not hint:
        return []

    docs = db.query(Document).filter(
        Document.title.ilike(f"%{hint}%"),
        Document.processed == True,
    ).order_by(Document.title).all()
    if docs:
        return docs

    hint_norm = _normalize_match_key(hint)
    if not hint_norm:
        return []

    all_docs = db.query(Document).filter(Document.processed == True).order_by(Document.title).all()
    fuzzy: list[Document] = []
    for doc in all_docs:
        title_norm = _normalize_match_key(getattr(doc, "title", ""))
        if not title_norm:
            continue
        if hint_norm in title_norm or title_norm in hint_norm:
            fuzzy.append(doc)
    return fuzzy


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

    from app.utils.logging import get_logger
    logger = get_logger("domain_query")

    # --- Filter by category ---
    resolved_category, explicit_category_hint = _resolve_category_from_question_or_hints(question, categories, hints)
    if resolved_category is not None:
        count = db.query(Document).filter(
            Document.category_id == resolved_category.id,
            Document.processed == True,
        ).count()
        return (
            f"There are **{count} document{'s' if count != 1 else ''}** "
            f"in the **{resolved_category.name}** collection."
        )
    if explicit_category_hint:
        return f"I couldn't find a collection matching '{explicit_category_hint}'."

    # --- Filter by domain (registry exact match) ---
    dom = _resolve_domain_from_registry(db, question, hints)
    if _is_domain_scoped(q, hints, dom):
        logger.info("handle_count: detected_domain=%s", dom.name if dom else None)
        if not dom:
            return _domain_not_found_response(hints)

        query = (
            db.query(func.count(func.distinct(Document.id)))
            .filter(Document.domain_id == dom.id, Document.processed == True)
        )
        doc_type = hints.get("doc_type")
        if doc_type:
            query = query.filter(Document.doc_type == doc_type)

        count = query.scalar() or 0
        logger.info("SQL: SELECT COUNT(DISTINCT documents.id) WHERE domain_id=%s", dom.id)
        logger.info("Row count returned: %s", count)
        return f"There are **{count} document{'s' if count != 1 else ''}** under the **{dom.name}** domain."

    # --- Filter by doc type keyword ---
    doc_type = hints.get("doc_type")
    if doc_type:
        kw_map = {
            "proposal":   ["proposal"],
            "case_study": ["case stud", "case_stud"],
            "solution":   ["solution", "service"],
            "policy":     ["policy", "policies"],
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
            total = sum(
                db.query(Document).filter(
                    Document.category_id == cat.id,
                    Document.processed == True,
                ).count()
                for cat in matching
            )
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
            breakdown.append(f"- **{cat.name}**: {c}")

    answer = f"There are **{total} document{'s' if total != 1 else ''}** in total."
    if breakdown:
        answer += "\n\n" + "\n".join(breakdown)
    return answer


def handle_classification(
    question: str,
    db: Session,
    categories: list[Category],
    entity: str | None,
    hints: dict[str, Any] | None = None,
) -> str | None:
    """
    Handle CLASSIFICATION intent.
    Returns formatted answer or *None* if it cannot resolve from metadata
    (caller should fall through to RAG in that case).
    """
    q = question.lower()
    h = hints or {}

    def _find_docs_by_title_guess(title_guess: str) -> list[Document]:
        guess = (title_guess or "").strip().strip("?.,!\"'()")
        guess = re.sub(r"\s+", " ", guess)
        if not guess:
            return []

        docs = (
            db.query(Document)
            .filter(Document.title.ilike(f"%{guess}%"), Document.processed == True)
            .order_by(Document.title)
            .limit(10)
            .all()
        )
        if docs:
            return docs

        stop = {"the", "a", "an", "is", "are", "was", "were", "does", "do", "under", "in", "of"}
        tokens = [t for t in re.split(r"\W+", guess.lower()) if len(t) >= 3 and t not in stop]
        if not tokens:
            return []
        conds = [Document.title.ilike(f"%{t}%") for t in tokens[:6]]
        return (
            db.query(Document)
            .filter(and_(*conds), Document.processed == True)
            .order_by(Document.title)
            .limit(10)
            .all()
        )

    # Verification mode: "Is <doc> under <domain>?"
    if h.get("verification") == "doc_under_domain" or (" under " in q and ("is " in q or q.startswith("is ") or q.startswith("are "))):
        dom = _resolve_domain_from_registry(db, question, h)
        if not dom:
            return _domain_not_found_response(h)

        doc_guess = h.get("doc_hint")
        if not doc_guess and " under " in q:
            left = question.split(" under ", 1)[0]
            doc_guess = re.sub(r"^\s*(?:is|are)\s+", "", left, flags=re.IGNORECASE).strip()

        if doc_guess:
            docs = _find_docs_by_title_guess(doc_guess)
            if not docs:
                return f"I couldn't find any documents with '{doc_guess}' in the title."

            lines: list[str] = []
            for doc in docs:
                actual_dom = doc.domain_ref.name if doc.domain_ref is not None else "Unassigned"
                ok = (doc.domain_id == dom.id)
                lines.append(
                    f"**{doc.title}** -> Domain: **{actual_dom}** ({'YES' if ok else 'NO'})"
                )
            if len(lines) == 1:
                only_ok = docs[0].domain_id == dom.id
                return (
                    f"Yes â€” **{docs[0].title}** is under **{dom.name}**."
                    if only_ok
                    else f"No â€” **{docs[0].title}** is under **{docs[0].domain_ref.name if docs[0].domain_ref is not None else 'Unassigned'}**, not **{dom.name}**."
                )
            return "I found multiple matching documents. Here are their domains:\n\n" + "\n".join(
                f"- {l}" for l in lines
            )

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
                elif doc.category:
                    cat_name = doc.category

                if doc.domain_ref is not None and doc.domain_ref.name:
                    domain_names = [doc.domain_ref.name]

                line = f"**{doc.title}** -> Category: **{cat_name}**"
                if domain_names:
                    line += f" | Domain: **{', '.join(domain_names)}**"
                results.append(line)

            if len(docs) == 1:
                return results[0]
            return f"Found {len(docs)} documents matching '{entity}':\n\n" + "\n".join(
                f"- {r}" for r in results
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
                titles = [f"- {d.title}" for d in docs]
                return (
                    f"The **{cat.name}** collection has "
                    f"**{len(docs)} document{'s' if len(docs) != 1 else ''}**:\n\n"
                    + "\n".join(titles)
                )
            return f"The **{cat.name}** collection exists but has no documents yet."

    return None  # Couldn't resolve from metadata -> fall through to RAG


def handle_listing(
    question: str,
    db: Session,
    categories: list[Category],
    hints: dict[str, Any],
) -> str:
    """Handle DOCUMENT_LISTING: return names of documents matching filters."""
    q = question.lower()
    q_norm = _normalize_match_key(question)
    category_constraint_present = bool(
        (hints.get("category_hint") or hints.get("target_category"))
        or _extract_category_hint_from_query(question)
        or re.search(r"\b(?:under|in|from)\b", q_norm)
    )

    include_domain_names = bool(
        re.search(r"\bdocuments?\b", q)
        and (
            "domain name" in q
            or "their domain" in q
            or re.search(r"\bwith\b.+\b(domains?|domins?)\b", q)
        )
    )

    if include_domain_names:
        rows = (
            db.query(Document.title, Domain.name)
            .outerjoin(Domain, Document.domain_id == Domain.id)
            .filter(Document.processed == True)
            .order_by(Document.title)
            .all()
        )
        if not rows:
            return "I don't have any documents in the registry yet."

        max_rows = 200
        shown = rows[:max_rows]
        lines = [
            f"- **{title}** -> Domain: **{domain_name or 'Unassigned'}**"
            for title, domain_name in shown
        ]
        suffix = f"\n\n...and {len(rows) - max_rows} more." if len(rows) > max_rows else ""
        return "Here are the documents with their domain names:\n\n" + "\n".join(lines) + suffix

    # Filter by category hint or explicit category mention in question.
    resolved_category, explicit_category_hint = _resolve_category_from_question_or_hints(question, categories, hints)
    if resolved_category is not None:
        docs = (
            db.query(Document)
            .filter(Document.category_id == resolved_category.id, Document.processed == True)
            .order_by(Document.title)
            .all()
        )
        if docs:
            titles = [f"- {d.title}" for d in docs]
            return (
                f"The **{resolved_category.name}** collection has **{len(docs)} document{'s' if len(docs) != 1 else ''}**:\n\n"
                + "\n".join(titles)
            )
        return f"The **{resolved_category.name}** collection exists but has no documents yet."
    if explicit_category_hint:
        return f"I couldn't find a collection matching '{explicit_category_hint}'."

    from app.utils.logging import get_logger
    logger = get_logger("domain_listing")

    dom = _resolve_domain_from_registry(db, question, hints)
    if _is_domain_scoped(q, hints, dom):
        logger.info(
            "handle_listing: detected_intent=DOCUMENT_LISTING, detected_domain=%s",
            dom.name if dom else None,
        )
        if not dom:
            return _domain_not_found_response(hints)

        docs_query = db.query(Document).filter(
            Document.domain_id == dom.id,
            Document.processed == True,
        )
        doc_type = hints.get("doc_type")
        if doc_type:
            docs_query = docs_query.filter(Document.doc_type == doc_type)

        docs = (
            docs_query.distinct(Document.id)
            .order_by(Document.title)
            .all()
        )
        logger.info(
            "SQL: SELECT DISTINCT documents.id, documents.title WHERE domain_id=%s", dom.id,
        )
        logger.info("Row count returned: %s", len(docs))
        if docs:
            titles = [f"- {d.title}" for d in docs]
            return (
                f"Found **{len(docs)} documents** under the **{dom.name}** domain:\n\n"
                + "\n".join(titles)
            )
        return f"The **{dom.name}** domain exists but has no documents yet."

    # Generic listing: return recent/top documents only when user did not request a specific collection.
    if category_constraint_present:
        return "I couldn't find a matching collection in your request."

    # Generic listing: return recent/top documents
    docs = db.query(Document).filter(Document.processed == True).order_by(Document.created_at.desc()).limit(20).all()
    if docs:
        titles = [f"- {d.title}" for d in docs]
        return "Here are some documents I have:\n\n" + "\n".join(titles)
    return "I don't have any documents in the registry yet."


def handle_domain_existence(
    question: str,
    db: Session,
    hints: dict[str, Any] | None = None,
) -> str:
    """Handle domain existence queries: "Is X domain available?" """
    hints = hints or {}
    domain_name = hints.get("domain") or hints.get("domain_hint")
    domain_name_norm = domain_name.lower().strip() if domain_name else None

    from app.utils.logging import get_logger
    logger = get_logger("domain_existence")
    logger.info(
        "handle_domain_existence: detected_intent=DOMAIN_EXISTENCE, detected_domain=%s",
        domain_name_norm,
    )

    if not domain_name_norm:
        return "Could you clarify which domain you are asking about?"

    dom = db.query(Domain).filter(Domain.name.ilike(domain_name_norm)).first()
    if dom:
        return f"Yes, the domain **{dom.name}** is available in our system."
    return f"Domain '{domain_name}' is not found in our system."


def handle_domain_listing(db: Session) -> str:
    """Handle domain listing queries: "Show all domains", "What domains do we have?" """
    from app.utils.logging import get_logger
    logger = get_logger("domain_listing")
    domains = db.query(Domain).all()
    logger.info("SQL: SELECT * FROM Domain")
    if not domains:
        return "No domains are registered in the system."
    lines = [f"- {d.name}" for d in domains]
    return "Here are all domains in the system:\n\n" + "\n".join(lines)


def handle_domain_query(
    question: str,
    db: Session,
    categories: list[Category],
    hints: dict[str, Any] | None = None,
) -> str:
    """Handle DOMAIN_QUERY strictly from the registry (SQL-only)."""
    hints = hints or {}

    from app.utils.logging import get_logger
    logger = get_logger("domain_query")

    q_norm = _normalize_registry_text(question)
    action = hints.get("domain_action")
    is_doc_query = re.search(r"\bdocuments?\b", q_norm) is not None

    # Topic-based registry listing (deterministic): "domains related to AI"
    m = re.search(
        r"\b(?:domains?|domins?)\b\s+(?:(?:are|is)\s+)?(?:related to|about|for|relevant to)\s+(.+?)\s*$",
        q_norm,
    )
    if m:
        topic = (m.group(1) or "").strip()
        if not topic:
            return "Could you clarify which topic you want related domains for?"

        domains = (
            db.query(Domain)
            .filter(
                or_(
                    Domain.name.ilike(f"%{topic}%"),
                    Domain.description.ilike(f"%{topic}%"),
                )
            )
            .order_by(Domain.name)
            .all()
        )
        logger.info("SQL: SELECT domains.* WHERE name/description ILIKE topic=%s", topic)
        if not domains:
            return f"No domains found related to '{topic}'."
        lines = [f"- {d.name}" for d in domains]
        return f"Domains related to **{topic}**:\n\n" + "\n".join(lines)

    # Global domain listing
    if (
        not is_doc_query
        and (
            action == "list"
            or re.search(r"\b(show|list)\b.+\b(domains?|domins?)\b", q_norm)
            or "what domains" in q_norm
        )
    ):
        return handle_domain_listing(db)

    # Domain count
    if action == "count" or "how many domains" in q_norm or "number of domains" in q_norm:
        cnt = db.query(func.count(Domain.id)).scalar() or 0
        logger.info("SQL: SELECT COUNT(domains.id)")
        return f"There are **{cnt} domain{'s' if cnt != 1 else ''}** in the system."

    # Domain existence check
    if action == "exists":
        dom = _resolve_domain_from_registry(db, question, hints)
        if dom:
            return f"Yes, the domain **{dom.name}** is available in our system."
        return _domain_not_found_response(hints)

    # Default: domain-specific document summary
    dom = _resolve_domain_from_registry(db, question, hints)
    logger.info("handle_domain_query: detected_domain=%s", dom.name if dom else None)
    if not dom:
        raw = hints.get("domain") or hints.get("domain_hint") or hints.get("target_domain")
        return (
            f"Domain '{raw}' is not found in our system."
            if raw
            else "Could you clarify which domain or topic you're asking about?"
        )

    rows = (
        db.query(Document.category_id, func.count(func.distinct(Document.id)))
        .filter(Document.domain_id == dom.id, Document.processed == True)
        .group_by(Document.category_id)
        .all()
    )
    logger.info(
        "SQL: SELECT category_id, COUNT(DISTINCT documents.id) WHERE domain_id=%s GROUP BY category_id",
        dom.id,
    )

    if not rows:
        return f"The **{dom.name}** domain exists but has no documents yet."

    cat_by_id = {c.id: c for c in categories}
    lines: list[str] = []
    total = 0
    for category_id, ccount in rows:
        total += int(ccount or 0)
        cat_name = cat_by_id.get(category_id).name if category_id in cat_by_id else "Uncategorized"
        lines.append(f"- **{cat_name}**: {int(ccount or 0)}")

    logger.info("Row count returned: %s", total)
    return (
        f"The **{dom.name}** domain has **{total} document{'s' if total != 1 else ''}** across these collections:\n\n"
        + "\n".join(lines)
    )


def handle_existence(
    question: str,
    db: Session,
    categories: list[Category],
    entity: str | None,
    hints: dict[str, Any] | None = None,
) -> str:
    """
    Handle EXISTENCE intent: does a document / category exist?

    Examples:
    - "Is there a cybersecurity policy PDF uploaded?"
    - "Do we have any HR documents?"
    - "Do we have a Benow proposal?"
    """
    hints = hints or {}
    q = question.lower()
    q_norm = _normalize_match_key(question)

    explicit_doc_check = bool(
        re.search(r"\b(is there|do we have|do you have|do u have|exists|are there)\b", q_norm)
    )
    doc_hint = (hints.get("doc_hint") or entity or _extract_doc_hint_from_query(question) or "").strip()

    if explicit_doc_check and doc_hint:
        docs = _find_docs_by_title_hint(db, doc_hint)

        if docs:
            if len(docs) == 1:
                return f"Yes, I have **{docs[0].title}**."
            top_titles = [f"- {d.title}" for d in docs[:5]]
            answer = (
                f"Yes, I found **{len(docs)} matching document{'s' if len(docs) != 1 else ''}** for **{doc_hint}**:\n\n"
                + "\n".join(top_titles)
            )
            if len(docs) > 5:
                answer += f"\n\n...and {len(docs) - 5} more."
            return answer

        return "Sorry, I don't have that document."

    # ---- Check by entity (document name) ----
    if entity:
        docs = db.query(Document).filter(
            Document.title.ilike(f"%{entity}%"),
            Document.processed == True,
        ).all()

        if docs:
            titles = [f"- {d.title}" for d in docs[:5]]
            plural = len(docs) != 1
            answer = (
                f"Yes, there {'are' if plural else 'is'} "
                f"**{len(docs)} document{'s' if plural else ''}** "
                f"related to **{entity}**:\n\n" + "\n".join(titles)
            )
            if len(docs) > 5:
                answer += f"\n\n...and {len(docs) - 5} more."
            return answer

    # ---- Check by search_type keyword (proposal, policy, etc.) ----
    search_type = hints.get("search_type", "").lower()
    if search_type and search_type != "document":
        type_kw_map = {
            "proposal":     ["proposal"],
            "case_study":   ["case stud", "case_stud"],
            "case studies": ["case stud", "case_stud"],
            "solution":     ["solution"],
            "service":      ["service"],
            "policy":       ["policy", "policies"],
            "pdf":          ["pdf"],
        }
        search_keywords = type_kw_map.get(search_type, [search_type])

        matching_docs = db.query(Document).filter(Document.processed == True).all()
        found = [
            doc for doc in matching_docs
            if any(kw in doc.title.lower() for kw in search_keywords)
            or (doc.description and any(kw in doc.description.lower() for kw in search_keywords))
        ]

        if found:
            titles = [f"- {d.title}" for d in found[:5]]
            label = search_type.replace("_", " ").replace(" stud", " study")
            plural = len(found) != 1
            answer = (
                f"Yes, there {'are' if plural else 'is'} "
                f"**{len(found)} {label} document{'s' if plural else ''}**:\n\n"
                + "\n".join(titles)
            )
            if len(found) > 5:
                answer += f"\n\n...and {len(found) - 5} more."
            return answer

    # ---- Check by category/type keywords in question ----
    type_kw = {
        "proposal":    ["proposal"],
        "case study":  ["case stud", "case_stud"],
        "case studies":["case stud", "case_stud"],
        "solution":    ["solution"],
        "service":     ["service"],
        "policy":      ["policy", "policies"],
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

    # ---- Generic: any documents exist? ----
    total = db.query(Document).filter(Document.processed == True).count()
    if total > 0:
        return f"Yes, I have **{total} document{'s' if total != 1 else ''}** across all collections."
    return "No, I don't have any documents in my registry yet."


def handle_conversational(question: str) -> str:
    """Handle greetings and conversational messages."""
    q = question.lower().strip()
    q_norm = re.sub(r"[^a-z0-9\s]", " ", q)
    q_norm = re.sub(r"\s+", " ", q_norm).strip()

    _exact_responses = {
        "thanks": "You're welcome.",
        "thank you": "You're welcome.",
        "bye": "Bye.",
        "goodbye": "Bye.",
        "see you": "Bye.",
        "ok": "Sure.",
        "okay": "Sure.",
        "sure": "Sure.",
        "great": "Great.",
    }
    if q_norm in _exact_responses:
        return _exact_responses[q_norm]

    if re.match(r"^(?:hi|hii+|hello|hey)(?:\s+mojo)?$", q_norm):
        return "Hello! How can I help?"
    if re.match(r"^how are (?:you|things)(?:\s+mojo)?$", q_norm) or re.match(r"^how s it going(?:\s+mojo)?$", q_norm):
        return "All good. How can I help?"
    if re.match(r"^(?:bye|goodbye|see you)(?:\s+mojo)?$", q_norm):
        return "Bye."
    if re.match(r"^(?:thanks|thank you)(?:\s+mojo)?$", q_norm):
        return "You're welcome."

    return "Hello! How can I help?"
