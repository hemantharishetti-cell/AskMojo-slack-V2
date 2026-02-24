from fastapi import APIRouter, File, Form, UploadFile, Depends, BackgroundTasks, status
from sqlalchemy.orm import Session, joinedload
from app.sqlite.database import get_db
from app.sqlite.models import Document, Category, Domain, User, DocumentUploadLog, QueryLog
from app.core.security import get_current_admin_user
from app.vector_logic.processor import process_document_background
# Optional third-party dependencies (graceful fallbacks if missing)
try:
    from openai import OpenAI as OpenAIClient
except ImportError:
    OpenAIClient = None
try:
    from toon import encode as toon_encode
except ImportError:
    toon_encode = None
try:
    import tiktoken as tiktoken_lib
except ImportError:
    tiktoken_lib = None
import re
from datetime import datetime
from collections import defaultdict
import time

from app.vector_logic.schemas import (
    DocumentResponse,
    DocumentUpdate,
    DocumentStatusResponse,
    VectorQueryRequest,
    VectorQueryResult,
    AskRequest,
    AskResponse,
    SourceChunk,
    AIDecisionResponse,
)
from app.vector_logic.description_generator import generate_description, refine_description
from app.vector_logic.vector_store import (
    list_collections,
    query_collection,
    query_collection_with_filter,  # ADD THIS LINE
    query_master_collection,
)
from app.vector_logic.doc_types import infer_doc_type_for_document, infer_doc_type_from_category_name
from app.vector_logic.intent_router import (
    QuestionIntent, classify_intent,
    QuestionAttribute, map_intent_to_attribute,
    handle_count, handle_classification, handle_existence, handle_conversational,
    handle_listing, handle_domain_query,
    recommend_solution, SOLUTION_KEYWORDS, handle_objection,
)

import json
import re
from app.core.config import settings
from fastapi import HTTPException
from uuid import uuid4
from os.path import splitext
import os
import shutil

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "app/uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["Documents"])


def _infer_preferred_doc_type_from_question(question: str) -> str | None:
    """
    Heuristic mapping from user question text to a preferred
    document type:
      - "proposal"
      - "case_study"
      - "solution"  (also for 'services')
    Returns None if no clear preference is detected.
    """
    q = (question or "").lower()

    # Case studies / success stories
    if ("case study" in q) or ("case studies" in q) or ("success story" in q) or ("success stories" in q):
        return "case_study"

    # Proposals
    if "proposal" in q or "proposals" in q:
        return "proposal"

    # Solutions / services
    if "solution" in q or "solutions" in q or "service" in q or "services" in q:
        return "solution"

    return None


def _extract_entity_from_question(question: str) -> str | None:
    """
    Extract primary entity/company/project name from the question.
    Uses heuristic patterns: possessives (X's), "for X", "about X", "in X".
    Returns the entity name or None.
    """
    import re as _re
    q = (question or "").strip()
    if not q:
        return None

    # Common words that look like entities but aren't
    _non_entities = {
        "DR", "QA", "CI", "CD", "API", "AWS", "GCP", "Azure", "EKS",
        "The", "This", "That", "What", "Which", "How", "Where", "When",
        "Who", "Why", "Does", "Is", "Are", "Can", "Could", "Should",
        "Would", "Will", "Do", "Has", "Have", "Had", "An", "No", "Yes",
        "Full", "All", "Any", "Each", "Every", "My", "Our", "Your",
    }

    # Pattern 1: "<Entity>'s" (possessive) — strongest signal
    match = _re.search(r"([A-Z][a-zA-Z0-9_\-]+)'s\b", q)
    if match and match.group(1) not in _non_entities:
        return match.group(1)

    # Pattern 2: "for <Entity>" / "about <Entity>" / "in <Entity>" / "of <Entity>"
    match = _re.search(r"(?:for|about|in|of|at|from|by)\s+([A-Z][a-zA-Z0-9_\-]+)", q)
    if match and match.group(1) not in _non_entities:
        return match.group(1)

    # Pattern 3: Look for CamelCase or unusual capitalised words (not at sentence start)
    words = q.split()
    for i, word in enumerate(words):
        if i == 0:
            continue  # Skip first word (always capitalised)
        clean = word.strip("?.,!\"'()")
        if clean and clean[0].isupper() and clean not in _non_entities and len(clean) > 2:
            return clean

    return None


def _humanize_title(title: str | None) -> str | None:
    """Convert internal-looking filenames to human-friendly names.

    Examples:
    - "BugBuster_Solutions" → "BugBuster"
    - "Fastrack Automation Presentation (1)" → "Fastrack Automation"
    - "MoolyAImpact - Updated (1)" → "MoolyAImpact"
    - strips extensions, underscores, numeric suffixes
    """
    if not title:
        return title
    t = title.strip()
    # Remove file extensions
    t = re.sub(r"\.(pdf|docx?|pptx?)$", "", t, flags=re.IGNORECASE)
    # Replace underscores/dashes with spaces
    t = t.replace("_", " ")
    # Remove common suffix words like "Solutions", "Presentation"
    t = re.sub(r"\b(Solutions?|Presentation|Report|Updated)\b", "", t, flags=re.IGNORECASE)
    # Remove parenthetical counters like (1), (2)
    t = re.sub(r"\s*\(\d+\)$", "", t)
    # Normalize whitespace
    t = re.sub(r"\s{2,}", " ", t).strip()
    # Special mappings for known solutions
    known_map = {
        "bugbuster": "BugBuster",
        "fastrack automation": "Fastrack Automation",
        "codeprobe": "CodeProbe",
        "moolyaimpact": "MoolyAImpact",
        "continuouscare": "ContinuousCare",
    }
    key = re.sub(r"\s+", " ", t).lower()
    if key in known_map:
        return known_map[key]
    return t


# -------------------------
# Orchestration helpers (Phase 1 of refactor)
# -------------------------
RESPONSE_TYPES = {
    "SALES_RECOMMENDATION": "SALES_RECOMMENDATION",
    "PROOF_STORY": "PROOF_STORY",
    "OBJECTION_HANDLING": "OBJECTION_HANDLING",
    "EXPLANATION": "EXPLANATION",
    "COMPARISON": "COMPARISON",
    "END_TO_END_STORY": "END_TO_END_STORY",
}


def select_role(intent, intent_hints: dict | None) -> str:
    """Assign role based on intent: Sales (default) or Pre-Sales.
    Keeps behavior stable while making the decision explicit.
    """
    si = (intent_hints or {}).get("sales_intent")
    if si == "Proof":
        return "Pre-Sales"
    # Objection and Discovery tend to be Sales-led
    return "Sales"


def select_response_type(intent, role: str, question: str, intent_hints: dict | None) -> str:
    q = (question or "").lower()
    # Objection shortcut (still handled early in flow)
    si = (intent_hints or {}).get("sales_intent")
    if si == "Objection":
        return RESPONSE_TYPES["OBJECTION_HANDLING"]
    if "compare" in q or " vs " in q:
        return RESPONSE_TYPES["COMPARISON"]
    if si == "Proof":
        return RESPONSE_TYPES["PROOF_STORY"]
    # Default path: Sales → recommendation; otherwise explanation
    if role == "Sales":
        return RESPONSE_TYPES["SALES_RECOMMENDATION"]
    return RESPONSE_TYPES["EXPLANATION"]


def build_constraints(role: str, response_type: str) -> dict:
    return {
        "banned_phrases": [
            "According to", "The document says", "as per the document",
        ],
        "avoid_internal_filenames": True,
        "experience_framing": role == "Sales",
        "structure": response_type,
    }

def build_proof_snippet(chunks: list[dict]) -> str | None:
    """Extract a concise Proof snippet from retrieved chunks.

    Looks for patterns indicating Domain, Scale, Problem, Outcome and returns
    a formatted block or None if nothing credible found.
    """
    if not chunks:
        return None

    domain = None
    scale = None
    problem = None
    outcome = None

    # Simple heuristics: search for key phrases and numbers
    for c in chunks:
        text = ((c.get("chunk_text") or "") + " " + (c.get("document_title") or "")).lower()
        # Domain: look for common domain keywords
        if not domain:
            for d in ["fintech", "health", "healthcare", "bfsi", "bank", "finance", "saas", "healthcare"]:
                if d in text:
                    domain = d.capitalize()
                    break
        # Scale: look for mentions of users, transactions, tests, or numbers
        if not scale:
            m = re.search(r"(\d[\d,\.]*\s*(?:users|customers|transactions|tests|nodes|endpoints|devices))", text)
            if m:
                scale = m.group(1)
        # Problem: look for pain keywords
        if not problem:
            for p in ["bug", "buggy", "crash", "flaky", "failure", "failure rate", "downtime", "compliance", "slow", "latency"]:
                if p in text:
                    problem = p
                    break
        # Outcome: look for percent reductions or improvements
        if not outcome:
            m2 = re.search(r"(reduc(?:ed|tion) of\s+\d+%|\d+%\s+(?:reduction|improvement|increase)|achieved\s+\d+%|improved by\s+\d+%|zero\s+breach)", text)
            if m2:
                outcome = m2.group(1)

    # If we found at least one meaningful element, produce a snippet
    if any([domain, scale, problem, outcome]):
        parts = []
        if domain:
            parts.append(f"Domain: {domain}")
        if scale:
            parts.append(f"Scale: {scale}")
        if problem:
            parts.append(f"Problem: {problem}")
        if outcome:
            parts.append(f"Outcome: {outcome}")

        # Also include top 1-2 source titles for citation
        titles = []
        for c in chunks:
            t = _humanize_title(c.get("document_title") or c.get("source") or None)
            if t and t not in titles:
                titles.append(t)
            if len(titles) >= 2:
                break

        source_str = f"Source: {titles[0]}" if titles else "Source: [document]"
        if len(titles) > 1:
            source_str = "Sources: " + ", ".join(titles[:2])

        return " | ".join(parts) + "\n" + source_str

    return None


def generate_followups(chunks: list[dict], intent_hints: dict, sales_intent: str | None, confidence: int, proof_snippet: str | None) -> list[dict]:
    """Generate 1-3 concise follow-up suggestions based on context.

    Each suggestion is a dict: {"text": str, "type": str}
    """
    suggestions: list[dict] = []
    # Prefer short, actionable CTAs
    # If proof is available, offer to fetch case studies/examples
    if proof_snippet:
        suggestions.append({"text": "Would you like me to fetch the case studies/proofs shown above?", "type": "fetch_proof"})

    # Sales intent based suggestions
    si = sales_intent or (intent_hints.get("sales_intent") if intent_hints else None)
    if si == "Discovery":
        suggestions.append({"text": "Would you like a short checklist to stabilize production quickly?", "type": "offer_checklist"})
        suggestions.append({"text": "Shall I propose a 2-4 week pilot to stabilize critical flows?", "type": "offer_pilot"})
    elif si == "Solutioning":
        suggestions.append({"text": "Do you want an implementation plan or integration steps?", "type": "ask_deep"})
    elif si == "Decision":
        suggestions.append({"text": "Shall I prepare a proposal and pricing estimate?", "type": "request_pricing"})

    # Confidence-based fallback: if low confidence, ask to refine
    if confidence and confidence < 60:
        suggestions.insert(0, {"text": "I can dig deeper — would you like more detailed evidence or examples?", "type": "offer_deeper"})

    # Always limit to 3 suggestions and ensure uniqueness
    unique_texts = set()
    final = []
    for s in suggestions:
        if s["text"] not in unique_texts:
            unique_texts.add(s["text"])
            final.append(s)
        if len(final) >= 3:
            break

    return final


def build_context_blocks(summaries_toon_str: str, chunks_toon_str: str, refined_question: str, data_quality: str, quality_context: str, quality_warning: str) -> str:
    """Small helper to assemble Step-4 context area. Reason: centralize block assembly for readability."""
    return f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 📊 DATA QUALITY: {data_quality.upper()} {quality_context}{quality_warning}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 📄 DOCUMENT SUMMARIES
{summaries_toon_str}

## 📑 DOCUMENT CHUNKS (Your ONLY source material)
{chunks_toon_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ❓ USER QUESTION
{refined_question}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def build_prompt_header(answer_mode: str, role: str, response_type: str, core_fear: str | None = None) -> str:
    """Small helper to output the prompt header consistently. Reason: avoid duplication and drift."""
    header = (
        "\n\n" +
        f"## 📋 QUESTION TYPE: {answer_mode.upper()}\n" +
        f"## 👤 ROLE: {role}\n" +
        f"## 🧩 RESPONSE TYPE: {response_type}\n"
    )
    if core_fear:
        header += f"## ⚠️ CORE CONCERN: {core_fear.upper()}\n"
    return header


def evaluate_answer_simple(answer: str, role: str, response_type: str) -> dict:
    """Lightweight rubric to check structure/tone. Reason: pre-return self-eval without hard-coding outputs."""
    a = (answer or "").lower()
    checks = {}
    # Universal bans
    checks["no_banned_phrases"] = ("according to" not in a) and ("the document says" not in a)
    # Source line (humanized titles handled upstream, here we just require presence)
    checks["has_source_line"] = ("source:" in a)
    # Sales recommendation structure (soft check)
    if response_type == RESPONSE_TYPES.get("SALES_RECOMMENDATION"):
        checks["has_recommendation"] = ("recommendation" in a)
        checks["has_why"] = ("why" in a)
        checks["has_how"] = ("how" in a)
        checks["has_proof"] = ("proof" in a)
    # Bullet cap (avoid over-completeness)
    bullet_lines = [ln for ln in (answer or "").splitlines() if ln.strip().startswith(("•", "- "))]
    checks["bullet_cap_le_6"] = (len(bullet_lines) <= 6)
    passed = [k for k, v in checks.items() if v]
    failed = [k for k, v in checks.items() if not v]
    score = len(passed)
    return {"score": score, "passed": passed, "failed": failed, "checks": checks}


def build_refinement_instruction(failed_checks: list[str], role: str, response_type: str) -> str:
    """Produce a concise rubric-based revision instruction. Reason: one-shot confidence gate."""
    lines = [
        "Revise your previous answer to satisfy these constraints strictly:",
    ]
    if response_type == RESPONSE_TYPES.get("SALES_RECOMMENDATION"):
        lines.append("- Ensure sections: Recommendation, Why, How, Proof (short, persuasive).")
    if "no_banned_phrases" in failed_checks:
        lines.append("- Remove phrases like 'According to' or 'The document says'.")
    if "has_source_line" in failed_checks:
        lines.append("- End with a single 'Source:' line using human-friendly document titles.")
    if "bullet_cap_le_6" in failed_checks:
        lines.append("- Keep at most 3 bullets in any list; be concise.")
    # Always focus & top-1 path behaviorally
    lines.append("- Focus only on the user's core concern; expose only the single best path.")
    return "\n".join(lines)


def build_behavioral_directives(role: str, response_type: str, core_fear: str | None = None) -> str:
    """Directive block encoding the delta checklist. Reason: guide first-pass behavior without hard-coding outputs."""
    directives = [
        "- Infer the user's core concern (cost, speed, risk, scale, trust) and answer ONLY that concern.",
        "- Internally rank possible paths; expose ONLY the single best path (no lists of solutions).",
        "- Prefer impact language (outcomes/results) over capability listings.",
        "- Ensure completeness as a sequence: Start → Control → Outcome, and include one concrete dimension (scale, timeline, risk removed, or confidence gained).",
        "- Match depth to audience; keep 'how' abstract unless explicitly asked; use analogy/contrast over feature lists.",
        "- Treat proof as change-in-state: Context → Scale → Problem → Intervention → Outcome. Use ranges (e.g., 20–40%) instead of exact numbers.",
        "- Limit to at most 3 bullets/examples; stop once a decision is enabled; end with a confident, actionable CTA.",
        "- Universal checks: answer the question (not the topic), choose one clear path, end with confidence, sound natural in a live sales call."
    ]
    
    # Add core fear-specific guidance if detected
    if core_fear:
        fear_guidance = f"- Primary concern detected: {core_fear.upper()}. Prioritize messaging around {core_fear} impact."
        directives.insert(0, fear_guidance)
    
    return "\n".join(directives)


def infer_core_fear(question: str) -> str | None:
    """Detect the core concern/fear: cost, speed, risk, scale, or trust. Reason: focus on primary user concern."""
    q = (question or "").lower()
    # Cost/budget fear
    if any(w in q for w in ["cost", "expensive", "budget", "price", "afford", "financial", "roi", "investment"]):
        return "cost"
    # Speed/time fear
    if any(w in q for w in ["slow", "fast", "speed", "quick", "quickly", "delay", "time", "acceleration", "reduce time"]):
        return "speed"
    # Risk/stability/reliability fear
    if any(w in q for w in ["risk", "fail", "crash", "bug", "defect", "stability", "reliable", "safe", "security", "confidence", "unstable"]):
        return "risk"
    # Scale/growth fear
    if any(w in q for w in ["scale", "grow", "large", "handle", "capacity", "volume", "load", "users", "growth", "concurrent"]):
        return "scale"
    # Trust/proof fear
    if any(w in q for w in ["proof", "evidence", "customer", "case study", "success", "track record", "experience", "credentials"]):
        return "trust"
    return None


@router.post("/upload")
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    category_id: int | None = Form(None),  # Category ID from Category table
    domain_id: int = Form(...),  # Domain ID from Domain table (required)
    category: str | None = Form(None),  # Legacy: kept for backward compatibility
    description: str | None = Form(None),  # Optional description (if provided, will be used; otherwise AI-generated from full PDF)
    internal_only: bool = Form(False),  # Whether document is internal-only
    current_user: User = Depends(get_current_admin_user),  # Admin authentication required
    db: Session = Depends(get_db),
):
    """
    Upload a document (admin only).
    Supports category_id (from Category table) or legacy category string.
    """
    try:
        # Check for duplicate file name in the same domain
        duplicate_query = (
            db.query(Document)
            .filter(Document.file_name == file.filename)
            .filter(Document.domain_id == domain_id)
        )
        duplicate_doc = duplicate_query.first()
        if duplicate_doc:
            raise HTTPException(
                status_code=400,
                detail="File already exists"
            )
        # -----------------------------
        # 1. Validate file
        # -----------------------------
        if not file.filename:
            raise HTTPException(status_code=400, detail="Empty filename")

        # Validate file extension
        allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
        ext = splitext(file.filename)[1].lower()
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
            )

        # -----------------------------
        # 2. Validate category_id if provided
        # -----------------------------
        category_obj = None
        collection_name = "documents"  # Default collection name
        category_name = category  # Legacy category name
        
        if category_id:
            category_obj = db.query(Category).filter(Category.id == category_id).first()
            if not category_obj:
                raise HTTPException(
                    status_code=404,
                    detail=f"Category with id {category_id} not found"
                )
            if not category_obj.is_active:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot upload to inactive category: {category_obj.name}"
                )
            collection_name = category_obj.collection_name
            category_name = category_obj.name  # Use category name from database

        # -----------------------------
        # 2.5 Validate domain_id (required)
        # -----------------------------
        domain_obj = db.query(Domain).filter(Domain.id == domain_id).first()
        if not domain_obj:
            raise HTTPException(
                status_code=404,
                detail=f"Domain with id {domain_id} not found",
            )
        if not domain_obj.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot upload to inactive domain: {domain_obj.name}",
            )

        # -----------------------------
        # 3. Safe filename
        # -----------------------------
        safe_filename = f"{uuid4().hex}{ext}"
        file_path = UPLOAD_DIR / safe_filename

        # -----------------------------
        # 4. Save file to disk
        # -----------------------------
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # -----------------------------
        # 5. Generate description (always generate, unless explicitly provided)
        # Track time and token usage
        # -----------------------------
        import time
        upload_start_time = time.time()
        description_generation_time = None
        description_tokens_info = None
        
        if not description:
            print(f"Generating description from full PDF for document: {title}")
            desc_start_time = time.time()
            # Determine domain for description generation: prefer explicit domain selection
            domain_name = domain_obj.name if domain_obj else None
            if not domain_name and category_obj and category_obj.domains:
                # Fallback to first domain associated with category
                domain_name = category_obj.domains[0].name
            description, description_tokens_info = generate_description(
                title=title,
                category=category_name,
                file_path=str(file_path),
                openai_api_key=settings.openai_api_key,
                domain=domain_name
            )
            desc_end_time = time.time()
            description_generation_time = desc_end_time - desc_start_time
            print(f"Generated description: {description[:100]}...")
            if description_tokens_info:
                print(f"Description generation used {description_tokens_info.get('total_tokens', 0)} tokens in {description_generation_time:.2f}s")
        else:
            print(f"Using provided description for document: {title}")

        # -----------------------------
        # 6. Detect source type from extension
        # -----------------------------
        source_type_map = {
            '.pdf': 'pdf',
            '.doc': 'doc',
            '.docx': 'docx',
            '.txt': 'txt'
        }
        source_type = source_type_map.get(ext, 'pdf')

        # -----------------------------
        # 7. Create DB record
        # -----------------------------
        document = Document(
            title=title,
            category=category_name,  # Legacy field
            category_id=category_id,  # New field
            domain_id=domain_id,
            description=description,
            source_type=source_type,
            file_name=file.filename,  # original name
            file_path=str(file_path),  # stored path
            internal_only=internal_only,
            processed=False,
            uploaded_by=current_user.id,  # Use authenticated admin user's ID
        )

        # Persist a deterministic doc_type for SQL-only filters.
        try:
            if category_obj is not None:
                document.category_ref = category_obj
            document.doc_type = infer_doc_type_for_document(document, db)
        except Exception:
            # Never block uploads on doc_type inference.
            document.doc_type = "other"

        db.add(document)
        db.commit()
        db.refresh(document)

        # -----------------------------
        # 7.5. Create upload log entry with time and token tracking
        # -----------------------------
        upload_end_time = time.time()
        upload_total_time = upload_end_time - upload_start_time
        
        upload_log = DocumentUploadLog(
            document_id=document.id,
            uploaded_by=current_user.id,
            title=title,
            file_name=file.filename,
            category_id=category_id,
            category=category_name,
            domain_id=domain_id,
            description_generated=(description is None or description == ""),
            description_length=len(description) if description else None,
            processing_started=False,
            processing_completed=False,
            upload_time_seconds=upload_total_time,
            description_generation_time_seconds=description_generation_time,
            description_tokens_used=description_tokens_info.get('total_tokens') if description_tokens_info else None,
            description_tokens_prompt=description_tokens_info.get('prompt_tokens') if description_tokens_info else None,
            description_tokens_completion=description_tokens_info.get('completion_tokens') if description_tokens_info else None
        )
        db.add(upload_log)
        db.commit()

        # Ensure category-domain association exists if both provided
        try:
            if category_id and domain_id:
                from app.sqlite.models import Category as CatModel, Domain as DomModel
                cat = db.query(CatModel).filter(CatModel.id == category_id).first()
                dom = db.query(DomModel).filter(DomModel.id == domain_id).first()
                if cat and dom and dom not in cat.domains:
                    cat.domains.append(dom)
                    db.commit()
        except Exception as _:
            db.rollback()

        # -----------------------------
        # 8. Schedule background processing with delay
        # Use collection_name from category if available, otherwise default
        # -----------------------------
        background_tasks.add_task(
            process_document_background,
            document_id=document.id,
            delay_seconds=settings.vector_processing_delay,
            collection_name=collection_name,  # Category-based collection
            persist_directory=None  # Will use default
        )

        return {
            "message": "Document uploaded successfully. Vector processing will start after delay.",
            "document_id": document.id,
            "title": document.title,
            "category": category_name,
            "category_id": category_id,
            "file_name": document.file_name,
            "processed": document.processed,
            "internal_only": document.internal_only,
            "processing_delay_seconds": settings.vector_processing_delay,
        }
    
    except HTTPException:
        # Re-raise HTTPExceptions (validation errors)
        raise
    except Exception as e:
        print(f"Error uploading document: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Error uploading document: {str(e)}"
        )


@router.get("/documents", response_model=list[DocumentResponse])
def list_documents(
    skip: int = 0,
    limit: int = 100,
    category: str | None = None,
    processed: bool | None = None,
    db: Session = Depends(get_db),
):
    """
    Get all documents with optional filtering and pagination.
    Includes uploader information for display.
    """
    query = db.query(Document).options(joinedload(Document.uploader))
    
    # Apply filters
    if category:
        query = query.filter(Document.category == category)
    if processed is not None:
        query = query.filter(Document.processed == processed)
    
    # Apply pagination
    documents = query.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    return documents


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: int, db: Session = Depends(get_db)):
    """
    Get a specific document by ID.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    return document


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
def get_document_status(document_id: int, db: Session = Depends(get_db)):
    """
    Get the processing status of a document.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    return document


@router.put("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    document_update: DocumentUpdate,
    db: Session = Depends(get_db),
):
    """
    Update a document by ID.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Update only provided fields
    update_data = document_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(document, field, value)
    
    db.commit()
    db.refresh(document)
    return document


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_admin_user),  # Admin authentication required
    db: Session = Depends(get_db),
):
    """
    Delete a document by ID (admin only).
    This will delete:
    1. The document from SQLite database (cascade will handle related records)
    2. All chunks from ChromaDB category-based collection
    3. Document metadata from ChromaDB master_docs collection
    4. The physical file from disk
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Get collection name from category if available
    collection_name = None
    if document.category_id:
        # Get category to find collection name
        from app.sqlite.models import Category
        category = db.query(Category).filter(Category.id == document.category_id).first()
        if category:
            collection_name = category.collection_name
    elif document.category:
        # Fallback to legacy category string
        collection_name = document.category.lower().replace(" ", "_")
    
    # Delete from ChromaDB first (before deleting from SQLite)
    try:
        from app.vector_logic.vector_store import delete_document_from_chromadb
        delete_document_from_chromadb(
            document_id=document_id,
            collection_name=collection_name,
            persist_directory=None  # Use default
        )
    except Exception as e:
        print(f"Error deleting from ChromaDB: {e}")
        # Continue with SQLite deletion even if ChromaDB deletion fails
        # This ensures the document is removed from the database
    
    # Delete the physical file if it exists
    if document.file_path:
        file_path = Path(document.file_path)
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Error deleting file {file_path}: {str(e)}")
                # Continue with DB deletion even if file deletion fails
    
    # Delete related records that have foreign keys to this document
    # 1. Delete DocumentUploadLog records
    from app.sqlite.models import DocumentUploadLog
    upload_logs = db.query(DocumentUploadLog).filter(
        DocumentUploadLog.document_id == document_id
    ).all()
    for log in upload_logs:
        db.delete(log)
    print(f"Deleted {len(upload_logs)} upload log(s) for document {document_id}")
    
    # 2. Delete QuerySource records (these reference documents via document_id)
    from app.sqlite.models import QuerySource
    query_sources = db.query(QuerySource).filter(
        QuerySource.document_id == document_id
    ).all()
    for source in query_sources:
        db.delete(source)
    print(f"Deleted {len(query_sources)} query source(s) for document {document_id}")
    
    # 3. Delete PDFExtractionCache records
    from app.sqlite.models import PDFExtractionCache
    extraction_caches = db.query(PDFExtractionCache).filter(
        PDFExtractionCache.document_id == document_id
    ).all()
    for cache in extraction_caches:
        db.delete(cache)
    print(f"Deleted {len(extraction_caches)} extraction cache record(s) for document {document_id}")
    
    # 4. Delete ExtractedContent records
    from app.sqlite.models import ExtractedContent
    extracted_contents = db.query(ExtractedContent).filter(
        ExtractedContent.document_id == document_id
    ).all()
    for content in extracted_contents:
        db.delete(content)
    print(f"Deleted {len(extracted_contents)} extracted content record(s) for document {document_id}")
    
    # 5. Delete DocumentChunk records
    from app.sqlite.models import DocumentChunk
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).all()
    for chunk in chunks:
        db.delete(chunk)
    print(f"Deleted {len(chunks)} chunk(s) for document {document_id}")
    
    # 6. Delete DocumentVersion records
    from app.sqlite.models import DocumentVersion
    versions = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).all()
    for version in versions:
        db.delete(version)
    print(f"Deleted {len(versions)} version(s) for document {document_id}")
    
    # Commit the deletions of all related records first
    db.commit()
    
    # Now delete from database (cascade will handle related records: DocumentVersion, DocumentChunk, etc.)
    db.delete(document)
    db.commit()
    
    print(f"Successfully deleted document {document_id} from SQLite and ChromaDB")
    return None


@router.get("/collections", response_model=list[dict])
def get_vector_collections():
    """
    List available ChromaDB collections.
    """
    return list_collections()


@router.post("/search", response_model=list[VectorQueryResult])
def search_vector_store(
    payload: VectorQueryRequest,
):
    """
    Query the vector database using a natural language query.

    - If category is provided, it is used as the collection name (normalized).
    - Otherwise, searches the default "documents" collection.
    """
    # Determine collection name from category
    if payload.category:
        collection_name = payload.category.lower().replace(" ", "_")
    else:
        collection_name = "documents"

    try:
        results = query_collection(
            query_text=payload.query,
            collection_name=collection_name,
            n_results=payload.top_k,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Chroma returns batched results; we only query with a single embedding
    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    response: list[VectorQueryResult] = []
    for idx, doc_id in enumerate(ids):
        response.append(
            VectorQueryResult(
                id=doc_id,
                document=documents[idx],
                score=float(distances[idx]),
                metadata=metadatas[idx] if metadatas and idx < len(metadatas) else {},
            )
        )

    return response


@router.post("/{document_id}/refine-description", response_model=DocumentResponse)
def refine_document_description(
    document_id: int,
    db: Session = Depends(get_db),
):
    """
    Refine/regenerate the document description using OpenAI API.
    This implements the feedback loop for description improvement.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Refine the description
    print(f"Refining description for document {document_id}: {document.title}")
    # Determine domain from category
    domain_name = None
    if document.category_id:
        cat = db.query(Category).filter(Category.id == document.category_id).first()
        # With many-to-many relationship, get first domain name from domains list
        if cat and cat.domains:
            domain_name = cat.domains[0].name
    refined_description = refine_description(
        current_description=document.description or f"Document: {document.title}",
        title=document.title,
        category=document.category,
        openai_api_key=settings.openai_api_key,
        domain=domain_name
    )
    
    # Update the document with refined description
    document.description = refined_description
    db.commit()
    db.refresh(document)
    
    print(f"Refined description: {refined_description[:100]}...")
    
    return document


def format_answer_for_slack(answer: str) -> str:
    """
    Convert markdown-formatted answer to Slack message format.
    Slack supports markdown but with some differences from standard markdown.
    """
    # Start with the original answer
    slack_message = answer
    
    # Convert markdown headings to Slack-friendly format
    # ### Heading -> *Heading* (bold, with spacing)
    slack_message = re.sub(r'^### (.+)$', r'\n*\1*\n', slack_message, flags=re.MULTILINE)
    # #### Heading -> *Heading* (bold, inline)
    slack_message = re.sub(r'^#### (.+)$', r'*\1*', slack_message, flags=re.MULTILINE)
    # ## Heading -> *Heading* (bold, with spacing)
    slack_message = re.sub(r'^## (.+)$', r'\n*\1*\n', slack_message, flags=re.MULTILINE)
    
    # Convert **bold** to *bold* (Slack uses single asterisks)
    slack_message = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', slack_message)
    
    # Convert bullet points - preserve indentation but use Slack-friendly bullets
    # Handle nested lists (3 spaces = 1 level, 6 spaces = 2 levels)
    slack_message = re.sub(r'^      - ', r'        • ', slack_message, flags=re.MULTILINE)  # 2nd level
    slack_message = re.sub(r'^   - ', r'      • ', slack_message, flags=re.MULTILINE)  # 1st level
    slack_message = re.sub(r'^- ', r'• ', slack_message, flags=re.MULTILINE)  # Top level
    
    # Format References section clearly with separator
    slack_message = re.sub(
        r'^### References$',
        r'\n─────────────────────────\n*References:*\n',
        slack_message,
        flags=re.MULTILINE
    )
    
    # Add horizontal rule equivalent (Slack doesn't have <hr>, use dashes)
    slack_message = re.sub(r'^---$', r'─────────────────────────', slack_message, flags=re.MULTILINE)
    
    # Clean up any extra blank lines (more than 2 consecutive)
    slack_message = re.sub(r'\n{3,}', r'\n\n', slack_message)
    
    # Ensure proper spacing at start and end
    slack_message = slack_message.strip()
    
    return slack_message


def convert_json_to_toon_and_show_savings(data: any, call_name: str, data_name: str = "Data") -> tuple[str, int, int]:
    """
    Convert JSON data to TOON format and display token savings.
    Returns: (toon_string, original_tokens, toon_tokens)
    """
    # Setup encoder (tiktoken if available, else simple whitespace tokenizer)
    if tiktoken_lib is not None:
        enc = tiktoken_lib.get_encoding("cl100k_base")
        def _count_tokens(text: str) -> int:
            return len(enc.encode(text))
    else:
        class _DummyEnc:
            def encode(self, s: str):
                # Rough approximation: split on whitespace
                try:
                    return s.split()
                except Exception:
                    return [s]
        enc = _DummyEnc()
        def _count_tokens(text: str) -> int:
            # Approximate: 1 token per 4 chars fallback
            return max(1, len(text) // 4)
    
    # Convert to JSON string for comparison
    json_str = json.dumps(data, indent=2)
    original_tokens = _count_tokens(json_str)
    
    # Convert to TOON format
    try:
        if toon_encode is None:
            raise RuntimeError("toon library not installed")
        toon_str = toon_encode(data)
        # Ensure it's a string
        if isinstance(toon_str, bytes):
            toon_str = toon_str.decode('utf-8')
        elif not isinstance(toon_str, str):
            toon_str = str(toon_str)
    except Exception as e:
        print(f"Warning: Could not encode {data_name} to TOON for {call_name}: {e}. Using JSON.")
        toon_str = json_str
    
    # Count tokens for TOON format (ensure it's a string)
    if not isinstance(toon_str, str):
        toon_str = str(toon_str)
    toon_tokens = _count_tokens(toon_str)
    
    return toon_str, original_tokens, toon_tokens


@router.post("/ask", response_model=AskResponse)
def ask_question(
    request: AskRequest,
    db: Session = Depends(get_db),
):
    import time
    import json
    start_time = time.time()
    """
    Answer a user's question using AI-guided multi-stage retrieval with new flow:
    
    Step 1: Pass all categories/collections with descriptions → AI decides which collections to fetch,
            modifies user query, generates top_k_documents
    
    Step 2: Filter docs by collection name and query from Step 1 → Search master_docs collection
            → For each relevant doc, generate per-document parameters (top_k_chunks_per_document,
            search_strategy, response_length, response_depth, estimated_tokens)
    
    Step 3: Find relevant chunks based on document name, collection name, top_k_chunks_per_document
            (dynamic per doc from Step 2) → Choose model selection
    
    Step 4: Generate final answer
    
    All JSON data is converted to TOON format for token efficiency.
    Comprehensive token usage and savings logging is included.
    """
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenAI API key not configured"
        )
    if OpenAIClient is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenAI library is not installed. Please install 'openai' to use this endpoint."
        )
    client = OpenAIClient(api_key=settings.openai_api_key)
    
    # Initialize token tracking and API call responses
    token_usage_tracker = {
        "calls": [],
        "total_json_tokens": 0,
        "total_toon_tokens": 0,
        "total_savings": 0,
        "total_savings_percent": 0.0
    }
    api_call_responses = []  # Store responses from each API call
    
    # Initialize implementation timing
    start_time = time.time()
    
    try:
        # Initialize token encoder
        if tiktoken_lib is not None:
            enc = tiktoken_lib.get_encoding("cl100k_base")
        else:
            class _DummyEnc2:
                def encode(self, s: str):
                    try:
                        return s.split()
                    except Exception:
                        return [s]
            enc = _DummyEnc2()
        
        # ====================================================================
        # CONVERSATION CONTEXT ANALYSIS
        # Analyze conversation history and query to determine optimal settings
        # ====================================================================
        conversation_history = request.conversation_history or []
        conversation_length = len(conversation_history)
        is_follow_up = conversation_length > 0
        is_clarification = any(
            word in request.question.lower() 
            for word in ["what do you mean", "can you explain", "clarify", "elaborate", "more details", "again"]
        )
        
        # Analyze query complexity for dynamic model/token selection
        query_length = len(request.question)
        question_words = ["what", "how", "why", "when", "where", "who", "which", "explain", "describe", "tell me"]
        has_complex_question = any(word in request.question.lower() for word in question_words)
        is_simple_greeting = request.question.lower().strip() in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]
        
        print(f"Conversation Context: History={conversation_length} messages, Follow-up={is_follow_up}, Clarification={is_clarification}, Complex={has_complex_question}")
        
        # ====================================================================
        # STEP 0: INTENT-BASED ROUTING WITH ATTRIBUTE-AWARE HARD CONSTRAINTS
        # (rule-based, zero cost)
        # 
        # CRITICAL FIX: Attribute classification ensures:
        # - METADATA_ONLY questions NEVER use embeddings or RAG
        # - FACTUAL questions NEVER try metadata-only lookups
        # - Wrong subsystems CANNOT answer questions
        # ====================================================================
        print(f"Step 0: Intent classification...")
        step0_start_time = time.time()
        
        # Get categories (needed for both Step 0 and Step 1)
        categories = db.query(Category).filter(Category.is_active == True).all()
        
        intent, intent_hints = classify_intent(request.question)
        attribute = map_intent_to_attribute(intent)
        
        # Extract entity early so metadata handlers can use it
        primary_entity = _extract_entity_from_question(request.question)
        
        print(f"  Intent: {intent.value} | Attribute: {attribute.value} | Entity: {primary_entity or 'none'} | Hints: {intent_hints}")

        # If this is a sales objection, handle with concise objection handler first
        sales_intent = intent_hints.get("sales_intent")
        if sales_intent == "Objection":
            ob_resp = handle_objection(request.question)
            if ob_resp:
                print("  [Sales Objection Handler] returning templated response")
                return AskResponse(answer=ob_resp, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- HARD CONSTRAINT: METADATA_ONLY attributes → NO RAG ALLOWED ----
        if attribute == QuestionAttribute.METADATA_ONLY:
            answer_text = handle_conversational(request.question)
            print(f"  [HARD STOP] Step 0: METADATA_ONLY→CONVERSATIONAL")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- HARD CONSTRAINT: DOCUMENT_COUNT → DATABASE ONLY ----
        if attribute == QuestionAttribute.DOCUMENT_COUNT:
            answer_text = handle_count(request.question, db, categories, intent_hints)
            print(f"  [HARD STOP] Step 0: DOCUMENT_COUNT (metadata only, no embeddings, no RAG)")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- HARD CONSTRAINT: DOCUMENT_EXIST → CHECK REGISTRY ONLY ----
        if attribute == QuestionAttribute.DOCUMENT_EXIST:
            answer_text = handle_existence(
                request.question, db, categories, primary_entity, intent_hints
            )
            print(f"  [HARD STOP] Step 0: DOCUMENT_EXIST (registry only, no RAG, no hallucination)")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- HARD CONSTRAINT: DOCUMENT_CATEGORY → REGISTRY ONLY ----
        if attribute == QuestionAttribute.DOCUMENT_CATEGORY:
            answer_text = handle_classification(request.question, db, categories, primary_entity)
            if answer_text is not None:
                print(f"  [HARD STOP] Step 0: DOCUMENT_CATEGORY (registry only, no RAG)")
                return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
            else:
                # Even if classification couldn't resolve, don't use RAG for category questions
                print(f"  [HARD STOP] Step 0: DOCUMENT_CATEGORY could not resolve - returning 'not found'")
                return AskResponse(
                    answer="I couldn't determine the category/domain classification from the registry. Could you rephrase your question?",
                    token_usage=None,
                    toon_savings=None,
                    api_calls=[]
                )

        # ---- HARD CONSTRAINT: DOCUMENT_LISTING → REGISTRY ONLY ----
        if attribute == QuestionAttribute.DOCUMENT_LISTING:
            answer_text = handle_listing(request.question, db, categories, intent_hints)
            print(f"  [HARD STOP] Step 0: DOCUMENT_LISTING (registry only, no RAG)")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])

        # ---- HARD CONSTRAINT: DOMAIN_QUERY → REGISTRY ONLY ----
        if attribute == QuestionAttribute.DOMAIN_QUERY:
            answer_text = handle_domain_query(request.question, db, categories, intent_hints)
            print(f"  [HARD STOP] Step 0: DOMAIN_QUERY (registry only, no RAG)")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- DOCUMENT_REFERENCE: Find document metadata first, then optional RAG ----
        # REGISTRY-FIRST: Document reference queries must be answered from the
        # registry if possible. Do NOT call RAG for DOCUMENT_REFERENCE queries.
        if attribute == QuestionAttribute.DOCUMENT_REFERENCE:
            answer_text = handle_existence(
                request.question, db, categories, primary_entity, intent_hints
            )
            print(f"  [HARD STOP] Step 0: DOCUMENT_REFERENCE (registry-first, no RAG)")
            return AskResponse(answer=answer_text, token_usage=None, toon_savings=None, api_calls=[])
        
        # ---- FACTUAL queries → RAG PIPELINE ONLY ----
        if attribute == QuestionAttribute.FACTUAL:
            print(f"  Step 0: Proceeding to RAG pipeline (attribute=FACTUAL)")
            print(f"Step 0 Completed in {time.time() - step0_start_time:.2f}s")
        else:
            # Unexpected attribute, log warning
            print(f"  WARNING: Unexpected attribute {attribute.value}, proceeding to RAG")
            print(f"Step 0 Completed in {time.time() - step0_start_time:.2f}s")
        
        # ====================================================================
        # STEP 1: Get all categories/collections with descriptions
        # AI decides which collections to fetch, modifies query, generates top_k_documents
        # ====================================================================
        # Defensive gate: Never proceed to the RAG/collection-selection step
        # unless the attribute is explicitly FACTUAL. Step 0 should have
        # already returned for metadata-only attributes; this is a safety
        # net to prevent unintended RAG calls.
        if attribute != QuestionAttribute.FACTUAL:
            print(f"Defensive gate: attribute={attribute.value} is not FACTUAL - aborting RAG execution")
            return AskResponse(
                answer=(
                    "This request was routed to a non-factual handler and should be "
                    "answered from the registry. If you received this message, please rephrase or check the registry."
                ),
                token_usage=None,
                toon_savings=None,
                api_calls=api_call_responses,
            )

        print(f"Step 1: Getting all categories/collections and determining which to fetch...")
        step1_start_time = time.time()
        
        # Categories already loaded in Step 0
        categories_data = []
        for cat in categories:
            categories_data.append({
                "collection_name": cat.collection_name,
                "category_name": cat.name,
                "domains": [d.name for d in cat.domains] if cat.domains else [],
                "description": cat.description or "No description available"
            })
        
        # Convert categories to TOON format for Step 1
        categories_toon, step1_categories_json, step1_categories_toon = convert_json_to_toon_and_show_savings(
            categories_data, "Step 1: Collection Selection", "Categories"
        )
        
        # Build Step 1 prompt with categories in TOON format
        if not categories_data:
            # No categories available, return early
            return AskResponse(
                answer="No categories are available. Please create categories first before asking questions.",
                token_usage=None,
                toon_savings=None,
                api_calls=[]
            )
        
        # Build conversation context for Step 1
        conversation_context = ""
        if conversation_history:
            conversation_context = "\n\nPrevious Conversation Context:\n"
            for i, msg in enumerate(conversation_history[-3:]):  # Last 3 messages for context
                role = msg.get("role", "user")
                content = msg.get("content", "")
                conversation_context += f"{role.capitalize()}: {content}\n"
            conversation_context += "\nNote: This is a follow-up question. Consider the conversation context when selecting collections."
        
        step1_prompt = f"""CRITICAL FIRST STEP: Determine if the user's question is actually RELATED to any of the available collections/data.

You have access to the following Categories/Collections (in TOON format):
{categories_toon}

User Question: {request.question}{conversation_context}

ANALYSIS PROCESS:

1. **RELEVANCE CHECK (MOST IMPORTANT)**:
   - First, determine if the question is AT ALL related to the available collections/data
   - Compare the question against each category's name and description
   - Questions that are NOT related include:
     * Simple greetings ONLY: "hi", "hello", "hey", "good morning", etc. (without any content question)
     * Pure conversational ONLY: "thanks", "okay", "bye", etc. (without any content question)
     * General knowledge questions completely unrelated to your collections (e.g., "what is the capital of France")
   - Questions that ARE RELATED and MUST proceed (proceed_to_step2 = true):
     * **Meta-queries about your data**: "what data do you have", "what documents are available", "what information is in your database", "what can you help with"
     * **Questions about collections**: "what collections do you have", "how many documents in [collection]", "what's in [collection]"
     * **Questions about document names/titles**: "what are the names of documents", "list the documents", "what documents are available"
     * **Questions asking "what is [topic]" or "tell me about [topic]"**: ALWAYS proceed - search documents to find information about the topic, even if topic isn't explicitly in category descriptions. The topic might be in document content/titles.
     * **Questions about topics mentioned in category descriptions**: Match against category names/descriptions
     * **Questions seeking information that could be in those collections**: Any query that might have answers in your documents - BE LIBERAL here
     * **Follow-up questions about previously discussed topics from documents**
     * **ANY question containing words/phrases that match category names or descriptions**
     * **ANY question asking for explanation/definition/information about something**: "what is X", "explain X", "tell me about X" - these should search documents

2. **IF NOT RELATED TO COLLECTIONS**:
   - Set proceed_to_step2 = false
   - Set selected_collections = [] (empty list)
   - Provide an appropriate direct_answer:
     * For greetings: Friendly greeting like "Hello! 👋 I'm ASKMOJO, your AI assistant. I can help you find information from your documents. What would you like to know?"
     * For conversational: Appropriate conversational response
     * For unrelated questions: "I can only answer questions related to the documents in my knowledge base. Based on your available collections, I can help with [mention relevant topics from collections]. How can I assist you?"

3. **IF RELATED TO COLLECTIONS**:
   - **ALWAYS proceed if the question asks about:**
     * What data/documents you have → Select all relevant collections, refine query to find document metadata/titles
     * Document names/titles → Select relevant collections, refine to search for document titles
     * Content in collections (e.g., "what is aftershoot") → Select relevant collections, refine query appropriately
     * Count of documents → Select relevant collections, refine query to find document count
   - Select one or more relevant category collections from the available list above
   - For meta-queries about data/documents, you may need to select ALL relevant collections
   - Refine/modify the query optimized for searching the selected collections:
     * "what data ask mojo have" → "what documents and information are available"
     * "what is after shoot" → "aftershoot information" (search documents)
     * "how many business proposals" → "business proposals" (to count/search them)
     * "what are the names of business proposals" → "business proposal titles and names"

CRITICAL RULES:
- DO NOT select "master_docs" - it is a special internal collection, not a category
- DO NOT proceed with document retrieval ONLY for pure greetings/conversational (no content question): "hi", "hello", "thanks", "bye"
- **MUST proceed (proceed_to_step2 = true) if question asks about:**
  * Available data/documents ("what data", "what documents", "what information")
  * Document names/titles ("what are the names", "list documents")
  * Content that might be in collections - **ALWAYS proceed for "what is X" type questions** - search documents even if X isn't in category descriptions
  * Topics matching category names/descriptions
  * Any question starting with "what is", "tell me about", "explain", "what are" - these should search documents
- **Be VERY liberal with relevance**: If a question COULD be answered from your collections, proceed. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and analysis.
- If question is truly unrelated (no connection to any collection), set proceed_to_step2 = false
ANSWER_MODE DETECTION (set answer_mode field):
- "extract": Question asks for a SPECIFIC VALUE
  * Starts with "What [type|platform|region|scope|tool|cloud|objective|goal|problem]"
  * Starts with "Which [regions|tools|platform|components]"
  * Example: "What platform is in scope?" → answer_mode = "extract"

- "brief": Question is YES/NO or CONFIRMATION
  * Contains "Does X include", "Is X handled by", "Is X included", "Does X support"
  * Example: "Does the proposal include EKS?" → answer_mode = "brief"

- "summarize": Question asks for SUMMARY or LIST
  * Contains "Summarize", "bullet points", "in simple terms", "give a pitch", "one-paragraph"
  * Contains "list", "enumerate", "compare"
  * Example: "Summarize in 5 bullet points" → answer_mode = "summarize"

- "explain": ALL OTHER questions (default)
  * "Explain", "How does", "Compare", "What are the steps", "Why", "Tell me about", "Describe"
  * Example: "Explain how Vault and ACM work together" → answer_mode = "explain"

QUERY REFINEMENT GUIDANCE:
- For "WHAT IS X" questions (extract/explain): Refine to emphasize direct information retrieval
  * e.g., "What is Vault" → refine to "Vault: definition, purpose, and capabilities"
- For procedural questions (explain): Refine to emphasize sequence and steps
  * e.g., "How does this work" → refine to "How does this work step by step"
- For comparative questions (explain): Refine to emphasize comparison aspects
  * e.g., "Compare A and B" → refine to "comparison of A and B, differences and similarities"
- For yes/no questions (brief): Refine to emphasize confirmation points
  * e.g., "Does X include Y" → refine to "verification that X includes Y"
{{
    "selected_collections": [<list of collection_name strings from the available categories above, e.g., ["proposals", "contracts"]>],
    "refined_question": "<modified/refined question text optimized for the selected collections>",
    "top_k_documents": <number of documents to retrieve from master collection, typically 2-10>,
    "proceed_to_step2": true/false,
    "reasoning": "<brief explanation of which collections were selected and why, and how the query was refined>",
    "direct_answer": "<if proceed_to_step2 is false and query can be answered directly, provide answer here, otherwise null>",
    "skip_reason": "<if proceed_to_step2 is false, explain why>"
    "answer_mode": "<extract|brief|summarize|explain>"
}}"""

        # Track Step 1 prompt tokens
        step1_prompt_tokens = len(enc.encode(step1_prompt))
        
        # Calculate dynamic max_tokens for Step 1 based on context and query complexity
        # Let the model decide based on number of categories and query requirements
        num_categories = len(categories_data)
        query_length = len(request.question)
        
        # Base tokens for JSON structure + reasoning
        base_tokens = 400
        # Scale by number of categories (more categories = more reasoning needed)
        category_factor = min(num_categories * 30, 500)
        # Scale by query complexity (longer queries may need more detailed reasoning)
        query_complexity = min(query_length / 30, 300)
        # Add context factor if conversation history exists
        context_factor = min(conversation_context_length / 5, 200)
        
        # Calculate dynamic max_tokens (generous to allow model to decide)
        step1_max_tokens = base_tokens + category_factor + int(query_complexity) + int(context_factor)
        # Cap at reasonable limit (4096 for JSON responses, but allow more for complex cases)
        step1_max_tokens = max(500, min(step1_max_tokens, 8192))  # Generous cap, model can use what it needs
        
        print(f"Step 1 Dynamic Token Limit: {step1_max_tokens} (categories={num_categories}, query_length={query_length}, context_length={conversation_context_length})")
        
        # Call OpenAI for Step 1 - model decides based on context and complexity
        step1_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert information retrieval specialist with deep analytical capabilities. Your CRITICAL role is to determine if a user's question is related to available collections/data. IMPORTANT: Questions asking about 'what data/documents you have', 'document names', 'how many documents', or any content that could be in collections (e.g., 'what is [topic]') MUST proceed with document retrieval (proceed_to_step2 = true). ONLY set proceed_to_step2 = false for pure greetings ('hi', 'hello') or truly unrelated questions (e.g., 'what is the capital of France' when your collections are about business). Be LIBERAL with relevance - if a question could be answered from collections, proceed. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and analysis."
                },
                {
                    "role": "user",
                    "content": step1_prompt
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.2,  # Lower temperature for more precise, intelligent collection selection
            max_tokens=step1_max_tokens  # Dynamic limit based on context - model decides actual length needed
        )
        
        # Track Step 1 response tokens
        step1_response_tokens = step1_response.usage.total_tokens if hasattr(step1_response, 'usage') else 0
        step1_total_json = step1_prompt_tokens + step1_response_tokens + step1_categories_json
        step1_total_toon = step1_prompt_tokens + step1_response_tokens + step1_categories_toon
        step1_savings = step1_total_json - step1_total_toon
        step1_savings_percent = (step1_savings / step1_total_json * 100) if step1_total_json > 0 else 0
        
        # Parse Step 1 response
        step1_data = json.loads(step1_response.choices[0].message.content)
        
        # Extract Step 1 results (now includes decision) - DO THIS BEFORE LOGGING
        selected_collections = step1_data.get("selected_collections", [])
        refined_question = step1_data.get("refined_question", request.question)
        top_k_docs = step1_data.get("top_k_documents", 5)
        proceed_to_step2 = step1_data.get("proceed_to_step2", True)
        step1_reasoning = step1_data.get("reasoning", "")
        direct_answer = step1_data.get("direct_answer")
        skip_reason = step1_data.get("skip_reason")
        answer_mode = step1_data.get("answer_mode", "explain")  # Default to explain
        
        # Extract primary entity from question for entity-aware filtering later
        primary_entity = _extract_entity_from_question(request.question)
        if primary_entity:
            print(f"  Detected primary entity: '{primary_entity}'")
        
        # Track Step 1
        token_usage_tracker["calls"].append({
            "call_name": "Step 1: Collection Selection, Query Refinement & Decision",
            "json_tokens": step1_total_json,
            "toon_tokens": step1_total_toon,
            "savings": step1_savings,
            "savings_percent": step1_savings_percent
        })
        
        # Store Step 1 response with complete information
        step1_response_data = {
            "call_name": "Step 1: Collection Selection, Query Refinement & Decision",
            "request_prompt": step1_prompt,  # Store full prompt
            "response_content": step1_data,  # Full response JSON
            "model_used": "gpt-4o-mini",
            "tokens_used": step1_total_toon,
            "tokens_without_toon": step1_total_json,
            "savings": step1_savings,
            "savings_percent": step1_savings_percent,
            "max_tokens": step1_max_tokens,  # Dynamic limit based on context
            "temperature": 0.2,  # Lower temperature for more precise collection selection
            "selected_collections": selected_collections,
            "refined_question": refined_question,
            "top_k_documents": top_k_docs,
            "proceed_to_step2": proceed_to_step2,
            "reasoning": step1_reasoning
        }
        api_call_responses.append(step1_response_data)
        
        # SAFETY NET: Disabled — attribute routing is authoritative.
        # Do not override the model's `proceed_to_step2` decision here. Attribute
        # classification must control whether RAG is invoked. Log the situation
        # for observability but DO NOT change proceed_to_step2.
        _q_word_count = len(request.question.strip().split())
        _pure_greetings = {
            "hi", "hello", "hey", "good morning", "good afternoon",
            "good evening", "good night", "thanks", "thank you",
            "bye", "goodbye", "see you", "ok", "okay"
        }
        _is_pure_greeting = request.question.lower().strip() in _pure_greetings

        if not proceed_to_step2 and not _is_pure_greeting and _q_word_count > 2:
            print(f"  SAFETY NET: Disabled. attribute={attribute.value}, proceed_to_step2 remains {proceed_to_step2}")
        
        # Check if we should proceed (decision now included in Step 1 response) - DO THIS FIRST
        if not proceed_to_step2:
            print(f"Step 1 Decision: NOT proceeding to Step 2")
            print(f"  Reason: {skip_reason or 'Unknown'}")
            print(f"  Reasoning: {step1_reasoning}")
            
            # If AI provided a direct answer, use it
            if direct_answer:
                print(f"  Using AI-provided direct answer: {direct_answer[:100]}...")
                return AskResponse(
                    answer=direct_answer,
                    token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                    toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                    api_calls=api_call_responses
                )
            else:
                # Generate appropriate response based on query type
                question_lower = request.question.lower().strip()
                if question_lower in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "good night"]:
                    response = "Hello! 👋 I'm ASKMOJO, your AI assistant. I can help you find information from your documents. What would you like to know?"
                elif question_lower in ["thanks", "thank you"]:
                    response = "You're welcome! Feel free to ask if you need anything else."
                elif question_lower in ["bye", "goodbye", "see you"]:
                    response = "Goodbye! Feel free to come back if you need any help."
                else:
                    # Question not related to available collections
                    category_names = [cat.name for cat in categories] if categories else []
                    response = f"I can only answer questions related to the documents in my knowledge base. Based on your available collections, I can help with: {', '.join(category_names) if category_names else 'topics covered in your documents'}. How can I assist you?"
                
                return AskResponse(
                    answer=response,
                    token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                    toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                    api_calls=api_call_responses
                )
        
        # If proceeding, validate and filter selected_collections
        # Remove "master_docs" if it was incorrectly selected
        if "master_docs" in selected_collections:
            print(f"Warning: AI incorrectly selected 'master_docs'. Removing it from selected collections.")
            selected_collections = [c for c in selected_collections if c != "master_docs"]
        
        # Get valid collection names from categories (with normalization for matching)
        valid_collection_names = {cat.collection_name for cat in categories}
        _norm_coll = lambda s: s.strip().lower().replace(" ", "_").replace("-", "_")
        norm_to_valid = {_norm_coll(name): name for name in valid_collection_names}
        
        # Filter to only include valid category collections (with normalization fallback)
        normalized_selected = []
        for c in selected_collections:
            if c in valid_collection_names:
                normalized_selected.append(c)
            elif _norm_coll(c) in norm_to_valid:
                normalized_selected.append(norm_to_valid[_norm_coll(c)])
                print(f"  Normalized collection name: '{c}' → '{norm_to_valid[_norm_coll(c)]}'")
            else:
                print(f"  Warning: Collection '{c}' not found (even after normalization)")
        selected_collections = normalized_selected
        
        # If no valid collections selected but proceed_to_step2 is true, check if this is an error
        if not selected_collections and proceed_to_step2:
            print(f"Warning: No valid collections selected but proceed_to_step2 is true. This may indicate the query is not related to available collections.")
            # Don't use fallback - return appropriate response instead
            return AskResponse(
                answer=f"I couldn't find relevant collections for your question. Based on your available collections, I can help with: {', '.join([cat.name for cat in categories]) if categories else 'topics covered in your documents'}. Please ask a question related to these collections.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # Validate top_k_docs
        top_k_docs = max(1, min(50, top_k_docs))
        
        print(f"Step 1 Results:")
        print(f"  Selected Collections: {selected_collections}")
        print(f"  Refined Question: {refined_question}")
        print(f"  Top K Documents: {top_k_docs}")
        print(f"  Proceed to Step 2: {proceed_to_step2}")
        print(f"  Reasoning: {step1_reasoning}")
        
        print(f"Step 1 Decision: Proceeding to Step 2")
        print(f"Step 1 Completed in {time.time() - step1_start_time:.2f}s")
        
        # ====================================================================
        # STEP 2: Filter docs by collection name and query from Step 1
        # Search master_docs collection
        # For each relevant doc, generate per-document parameters
        # ====================================================================
        print(f"Step 2: Searching master_docs collection with refined query and filtering by selected collections...")
        step2_start_time = time.time()
        
        # Search master_docs collection
        # Expand search window to compensate for post-filtering by selected collections
        master_search_n = top_k_docs * 3 if selected_collections else top_k_docs
        master_search_n = min(master_search_n, 50)
        print(f"  Master search: requesting {master_search_n} results (base top_k={top_k_docs})")
        master_results = query_master_collection(
            query_text=refined_question,
            n_results=master_search_n
        )
        
        if not master_results.get("ids") or not master_results["ids"][0]:
            return AskResponse(
                answer="I couldn't find any relevant documents to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # Extract document IDs and filter by selected collections
        document_ids = [int(doc_id) for doc_id in master_results["ids"][0]]
        
        # Get full document info from SQLite with category and doc_type information
        documents = db.query(Document).filter(Document.id.in_(document_ids)).all()
        doc_dict = {doc.id: doc for doc in documents}
        
        # Infer logical doc_type for each document (proposal / case_study / solution / other)
        doc_types: dict[int, str] = {}
        for doc in documents:
            doc_types[doc.id] = infer_doc_type_for_document(doc, db)
        
        # Filter documents by selected collections
        filtered_documents: list[int] = []
        for doc_id in document_ids:
            if doc_id not in doc_dict:
                continue
            doc = doc_dict[doc_id]
            
            # Get collection name from category
            collection_name = None
            if doc.category_id:
                category = db.query(Category).filter(Category.id == doc.category_id).first()
                if category:
                    collection_name = category.collection_name
            elif doc.category:
                collection_name = doc.category.lower().replace(" ", "_").replace("-", "_")
            
            # Only include if collection is in selected_collections (or if no collections selected, include all)
            if not selected_collections or (collection_name and collection_name in selected_collections):
                filtered_documents.append(doc_id)
        
        # Soft boost by preferred document type (reorder, don't exclude)
        # This ensures preferred-type docs are checked first without losing other relevant docs
        preferred_doc_type = _infer_preferred_doc_type_from_question(refined_question)
        if preferred_doc_type:
            preferred_ids = [
                doc_id for doc_id in filtered_documents
                if doc_types.get(doc_id) == preferred_doc_type
            ]
            non_preferred_ids = [
                doc_id for doc_id in filtered_documents
                if doc_types.get(doc_id) != preferred_doc_type
            ]
            if preferred_ids:
                print(
                    f"  Soft-boosting preferred doc_type: {preferred_doc_type} "
                    f"({len(preferred_ids)} preferred + {len(non_preferred_ids)} other docs kept)"
                )
                filtered_documents = preferred_ids + non_preferred_ids

        # Soft boost by inferred solution (e.g., BugBuster, Fastrack Automation)
        preferred_solution = recommend_solution(refined_question)
        if preferred_solution:
            kw_list = SOLUTION_KEYWORDS.get(preferred_solution, [])
            preferred_solution_ids = []
            for doc_id in filtered_documents:
                doc = doc_dict.get(doc_id)
                if not doc:
                    continue
                text = ((doc.title or "") + " " + (doc.description or "")).lower()
                if any(kw in text for kw in kw_list):
                    preferred_solution_ids.append(doc_id)
            if preferred_solution_ids:
                other_ids = [d for d in filtered_documents if d not in preferred_solution_ids]
                filtered_documents = preferred_solution_ids + other_ids
                print(
                    f"  Soft-boosting preferred solution: {preferred_solution} "
                    f"({len(preferred_solution_ids)} preferred + {len(other_ids)} other docs kept)"
                )
        
        # ================================================================
        # ENTITY-AWARE DOCUMENT FILTERING
        # If the question is about a specific entity (company/project),
        # prioritize documents whose title contains that entity.
        # For extract/brief modes, ONLY keep entity-matching docs to cut noise.
        # ================================================================
        if primary_entity:
            entity_lower = primary_entity.lower()
            entity_matching_docs = [
                doc_id for doc_id in filtered_documents
                if doc_id in doc_dict and entity_lower in (doc_dict[doc_id].title or "").lower()
            ]
            if entity_matching_docs:
                non_entity_docs = [
                    doc_id for doc_id in filtered_documents
                    if doc_id not in entity_matching_docs
                ]
                print(f"  Entity filter: '{primary_entity}' matched {len(entity_matching_docs)} doc(s), "
                      f"{len(non_entity_docs)} other doc(s)")
                if answer_mode in ("extract", "brief"):
                    # For factual / yes-no questions, ONLY use entity-matching docs
                    filtered_documents = entity_matching_docs
                    print(f"  Entity filter: answer_mode={answer_mode} → ONLY entity-matching docs")
                else:
                    # For explain/summarize, put entity docs first but keep others
                    filtered_documents = entity_matching_docs + non_entity_docs
        
        if not filtered_documents:
            return AskResponse(
                answer="I couldn't find any relevant documents in the selected collections to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        print(f"  Found {len(filtered_documents)} documents in selected collections")
        print("Step 3: Starting chunk retrieval for filtered documents...")
        
        # Check if this is a meta-query about document counts, names, or available data
        question_lower = refined_question.lower()
        
        # For meta-queries about "what data/information do you have" - list all collections and counts
        # Tightened: short questions only + extra negative keywords to avoid intercepting content questions
        _meta_kw = ["what data", "what information", "what do you have", "what can you help"]
        _meta_exclude = ["is", "about", "regarding", "in", "for", "from", "does", "use", "contain", "include"]
        if (len(question_lower.split()) <= 8
            and any(kw in question_lower for kw in _meta_kw)
            and not any(w in question_lower for w in _meta_exclude)):
            # Get all collections with their document counts
            collection_info = []
            for cat in categories:
                doc_count = db.query(Document).filter(Document.category_id == cat.id).count()
                if doc_count > 0:
                    collection_info.append(f"{cat.name}: {doc_count} document{'s' if doc_count != 1 else ''}")
            
            if collection_info:
                answer_text = "I have access to the following collections:\n\n" + "\n".join(f"• {info}" for info in collection_info) + "\n\nWhat would you like to know about these documents?"
            else:
                answer_text = "I don't have any documents in my knowledge base yet. Please upload documents to get started."
            
            return AskResponse(
                answer=answer_text,
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # For meta-queries about counts, get actual count from database
        count_keywords = ["how many", "count of", "number of"]  # Removed "total" (too generic)
        target_keywords = ["document", "proposal", "collection", "case stud", "solution", "service"]
        # Guard: only intercept for pure counting questions, not content questions
        _count_exclude = ["in", "scope", "include", "contain", "use", "cover", "server", "tool",
                          "feature", "component", "about", "for", "from", "within", "cost", "price"]
        if (any(kw in question_lower for kw in count_keywords)
            and any(w in question_lower for w in target_keywords)
            and not any(w in question_lower for w in _count_exclude)
            and len(question_lower.split()) <= 10):
            
            if selected_collections:
                category_ids = [cat.id for cat in categories if cat.collection_name in selected_collections]
                if category_ids:
                    # Get all docs in selected categories to filter by type
                    docs_in_cats = db.query(Document).filter(Document.category_id.in_(category_ids)).all()
                    
                    # Also check for documents that match by legacy category name if they have no category_id
                    # This ensures we count older documents that might effectively belong to this collection
                    legacy_names = [cat.name for cat in categories if cat.id in category_ids]
                    if legacy_names:
                        legacy_docs = db.query(Document).filter(
                            Document.category_id == None,
                            Document.category.in_(legacy_names)
                        ).all()
                        # Add unique legacy docs to our list
                        existing_ids = {d.id for d in docs_in_cats}
                        for d in legacy_docs:
                            if d.id not in existing_ids:
                                docs_in_cats.append(d)
                    
                    if preferred_doc_type:
                        # Use the inferred type which checks title/filename as well
                        # IMPROVED LOGIC: Also check if the category itself matches the preferred type
                        # This handles cases where a document title might mislead inference (e.g., "Solution" in "Case Studies")
                        
                        # Optimization: Map category_id to category object to avoid DB queries in loop
                        categories_map = {c.id: c for c in categories}
                        
                        filtered_count = 0
                        
                        # Use inline logic to avoid potential DB queries in infer_doc_type_for_document
                        for d in docs_in_cats:
                            is_match = False
                            
                            # 1. Normalize label for title/filename check
                            label = (d.title + " " + (d.file_name or "")).strip().lower().replace("_", " ").replace("-", " ")
                            
                            # Check title against preferred type
                            if preferred_doc_type == "case_study" and (("case" in label and "study" in label) or "success story" in label):
                                is_match = True
                            elif preferred_doc_type == "proposal" and "proposal" in label:
                                is_match = True
                            elif preferred_doc_type == "solution" and ("solution" in label or "service" in label):
                                is_match = True
                                
                            # 2. If valid category exists, check if category type matches (using in-memory map)
                            if not is_match and d.category_id and d.category_id in categories_map:
                                cat = categories_map[d.category_id]
                                cat_type = infer_doc_type_from_category_name(cat.name or cat.collection_name)
                                if cat_type == preferred_doc_type:
                                    is_match = True
                            
                            # 3. Legacy string check
                            if not is_match and d.category:
                                cat_type = infer_doc_type_from_category_name(d.category)
                                if cat_type == preferred_doc_type:
                                    is_match = True
                            
                            if is_match:
                                filtered_count += 1
                        
                        doc_count = filtered_count
                        count_target = preferred_doc_type.replace("_", " ") + " documents"
                    else:
                        doc_count = len(docs_in_cats)
                    
                    collection_name_display = [cat.name for cat in categories if cat.collection_name in selected_collections]
                    collection_name_display = collection_name_display[0] if collection_name_display else selected_collections[0] if selected_collections else "documents"
                    
                    return AskResponse(
                        answer=f"I found {doc_count} {count_target} in the {collection_name_display.lower()} collection(s).",
                        token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                        toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                        api_calls=api_call_responses
                    )
        
        # For meta-queries about document names/titles, get actual titles from database
        # Tightened: use specific phrases to avoid catching content questions like "list the tools"
        _doc_listing_phrases = [
            "what are the names of documents", "what are the names of proposals",
            "what are the names of the documents", "what are the names of the proposals",
            "list the documents", "list the proposals", "list all documents",
            "list all proposals", "list documents", "list proposals",
            "name of documents", "name of proposals"
        ]
        if any(phrase in question_lower for phrase in _doc_listing_phrases):
            if selected_collections:
                category_ids = [cat.id for cat in categories if cat.collection_name in selected_collections]
                if category_ids:
                    all_docs = db.query(Document).filter(Document.category_id.in_(category_ids)).order_by(Document.title).all()
                    if all_docs:
                        doc_titles = [doc.title for doc in all_docs]
                        if len(doc_titles) > 50:
                            titles_text = ", ".join(doc_titles[:50]) + f", and {len(doc_titles) - 50} more documents."
                        else:
                            titles_text = ", ".join(doc_titles) + "."
                        
                        collection_name_display = [cat.name for cat in categories if cat.collection_name in selected_collections]
                        collection_name_display = collection_name_display[0] if collection_name_display else selected_collections[0] if selected_collections else "documents"
                        
                    return AskResponse(
                        answer=f"The documents available in the {collection_name_display.lower()} collection include: {titles_text}",
                            token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                            toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                            api_calls=api_call_responses
                        )
        
        # Build document summaries for Step 2 AI call (in TOON format)
        master_summary = []
        for idx, doc_id in enumerate(document_ids):
            if doc_id not in filtered_documents:
                continue
            if doc_id not in doc_dict:
                continue
            doc = doc_dict[doc_id]
            
            # Get collection name
            collection_name = None
            if doc.category_id:
                category = db.query(Category).filter(Category.id == doc.category_id).first()
                if category:
                    collection_name = category.collection_name
            elif doc.category:
                collection_name = doc.category.lower().replace(" ", "_").replace("-", "_")
            
            master_summary.append({
                "document_id": doc_id,
                "title": doc.title,
                "collection_name": collection_name or "documents",
                "description": doc.description or "",
                "relevance_score": float(master_results["distances"][0][idx]) if master_results.get("distances") else 0.0
            })
        
        # Convert to TOON for Step 2
        master_summary_toon, step2_summaries_json, step2_summaries_toon = convert_json_to_toon_and_show_savings(
            master_summary, "Step 2: Per-Document Parameter Generation", "Document Summaries"
        )
        
        # Step 2: Generate per-document parameters AND decide if we can answer from summaries
        # Enhanced with intelligent reasoning for better parameter selection
        step2_prompt = f"""You are an expert at information retrieval and question analysis. Your task is to intelligently determine optimal retrieval parameters by deeply understanding question complexity, information needs, and document relevance. Use sophisticated reasoning to evaluate whether summaries are sufficient (be conservative—only skip chunk retrieval if summaries genuinely contain all necessary detail). Apply critical thinking to optimize parameters for maximum answer quality. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and detailed parameter selection based on the complexity of the question and number of documents."""

        # Track Step 2 prompt tokens
        step2_prompt_tokens = len(enc.encode(step2_prompt))
        
        # Calculate dynamic max_tokens for Step 2 based on context and query complexity
        # Let the model decide based on number of documents and query requirements
        num_documents = len(master_summary)
        query_length = len(refined_question)
        
        # Base tokens for JSON structure + reasoning
        base_tokens = 600
        # Scale by number of documents (more documents = more parameter decisions needed)
        document_factor = min(num_documents * 150, 2000)
        # Scale by query complexity (complex queries may need more detailed reasoning per document)
        query_complexity = min(query_length / 20, 400)
        # Factor for detailed parameter reasoning (top_k_chunks, strategies, etc.)
        parameter_reasoning_factor = min(num_documents * 100, 1500)
        
        # Calculate dynamic max_tokens (generous to allow model to decide)
        step2_max_tokens = base_tokens + document_factor + int(query_complexity) + parameter_reasoning_factor
        # Cap at reasonable limit (generous for complex cases with many documents)
        step2_max_tokens = max(800, min(step2_max_tokens, 12288))  # Very generous cap, model can use what it needs
        
        print(f"Step 2 Dynamic Token Limit: {step2_max_tokens} (documents={num_documents}, query_length={query_length})")
        
        # Call OpenAI for Step 2 - model decides based on context and complexity
        step2_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert information retrieval specialist with advanced analytical capabilities. Your role is to intelligently determine optimal retrieval parameters by deeply understanding question complexity, information needs, and document relevance. Use sophisticated reasoning to evaluate whether summaries are sufficient (be conservative—only skip chunk retrieval if summaries genuinely contain all necessary detail). Apply critical thinking to optimize parameters for maximum answer quality. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and detailed parameter selection based on the complexity of the question and number of documents."
                    },
                    {
                        "role": "user",
                    "content": step2_prompt
                    }
                ],
                response_format={"type": "json_object"},
            temperature=0.25,  # Lower temperature for more precise, intelligent parameter selection
            max_tokens=step2_max_tokens  # Dynamic limit based on context - model decides actual length needed
        )
        
        # Track Step 2 response tokens
        step2_response_tokens = step2_response.usage.total_tokens if hasattr(step2_response, 'usage') else 0
        step2_total_json = step2_prompt_tokens + step2_response_tokens + step2_summaries_json
        step2_total_toon = step2_prompt_tokens + step2_response_tokens + step2_summaries_toon
        step2_savings = step2_total_json - step2_total_toon
        step2_savings_percent = (step2_savings / step2_total_json * 100) if step2_total_json > 0 else 0
        
        # Parse Step 2 response (now includes decision)
        step2_data = json.loads(step2_response.choices[0].message.content)
        
        # Extract decision and configs (now included in Step 2 response) - DO THIS BEFORE LOGGING
        #proceed_to_step3 = step2_data.get("proceed_to_step3", True)
        proceed_to_step3 = True  # ALWAYS proceed to chunk retrieval
        answer_from_summaries = step2_data.get("answer_from_summaries")
        doc_configs = step2_data.get("document_configs", [])
        
        # Track Step 2
        token_usage_tracker["calls"].append({
            "call_name": "Step 2: Per-Document Parameter Generation & Decision",
            "json_tokens": step2_total_json,
            "toon_tokens": step2_total_toon,
            "savings": step2_savings,
            "savings_percent": step2_savings_percent
        })
        
        # Store Step 2 response with complete information
        step2_response_data = {
            "call_name": "Step 2: Per-Document Parameter Generation & Decision",
            "request_prompt": step2_prompt,  # Store full prompt
            "response_content": step2_data,  # Full response JSON
            "model_used": "gpt-4o-mini",
            "tokens_used": step2_total_toon,
            "tokens_without_toon": step2_total_json,
            "savings": step2_savings,
            "savings_percent": step2_savings_percent,
            "max_tokens": step2_max_tokens,  # Dynamic limit based on context
            "temperature": 0.25,  # Lower temperature for more precise parameter selection
            "proceed_to_step3": proceed_to_step3,
            "document_configs": doc_configs,
            "documents_found": len(master_summary) if 'master_summary' in locals() else 0
        }
        api_call_responses.append(step2_response_data)
        
       # if not proceed_to_step3:
       #     print(f"Step 2 Decision: NOT proceeding to Step 3")
       #     print(f"  Reason: {step2_data.get('skip_reason', 'Unknown')}")
       #     print(f"  Confidence: {step2_data.get('confidence', 'N/A')}")
            
       #     if answer_from_summaries:
       #         # Use the answer provided by Step 2
       #         return AskResponse(
       #             answer=answer_from_summaries,
       #             token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
       #             toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
       #             api_calls=api_call_responses
       #         )
            
       #     # If no answer from summaries, return basic response
       #     return AskResponse(
       #         answer=f"Based on the available documents, I found {len(master_summary)} relevant document(s): {', '.join([d['title'] for d in master_summary[:3]])}. However, more detailed information is needed to fully answer your question. {step2_data.get('skip_reason', 'Please rephrase or provide more context.')}",
       #         token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
       #         toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
       #         api_calls=api_call_responses
       #     )
        
        print(f"Step 2 Decision: Proceeding to Step 3 (chunk retrieval)")
        print(f"  Reasoning: {step2_data.get('skip_reason', 'N/A')}")
        print(f"  Confidence: {step2_data.get('confidence', 'N/A')}")
        
        # Create per-document config dictionary (doc_configs already extracted above)
        doc_config_dict = {config["document_id"]: config for config in doc_configs}
        
        # Default values for documents not in config
        default_config = {
            "top_k_chunks_per_document": 10,
            "search_strategy": "selective",
            "response_length": "medium",
            "response_depth": "moderate",
            "estimated_tokens": 3000
        }
        
        print(f"Step 2 Results: Generated parameters for {len(doc_configs)} documents")
        print(f"Step 2 Completed in {time.time() - step2_start_time:.2f}s")
        
        # Log the per-document configurations for debugging lengthy question accuracy
        if doc_configs:
            print(f"Step 2 Per-Document Parameters Breakdown:")
            for idx, config in enumerate(doc_configs[:3]):  # Show first 3 for brevity
                print(f"  Doc {idx+1}: ID={config['document_id']}, chunks={config.get('top_k_chunks_per_document', 'N/A')}, "
                      f"strategy={config.get('search_strategy', 'N/A')}, depth={config.get('response_depth', 'N/A')}")
            if len(doc_configs) > 3:
                print(f"  ... and {len(doc_configs) - 3} more documents")
        
        # ====================================================================
        # STEP 3: Find relevant chunks based on document name, collection name,
        # top_k_chunks_per_document (dynamic per doc from Step 2)
        # Choose model selection
        # ====================================================================
        if 'filtered_documents' in locals() and filtered_documents:
            print(f"Step 3: Retrieving chunks for {len(filtered_documents)} documents (Optimized Batch Mode)...")
            step3_start_time = time.time()
            
            relevant_documents = []
            source_chunks = []
            
            # Map categories for faster lookup
            category_map = {}
            try:
                categories_list = db.query(Category).all()
                category_map = {c.id: c for c in categories_list}
            except Exception as e:
                print(f"Warning: Could not preload categories: {e}")

            # Group documents by collection to batch queries
            docs_by_collection = defaultdict(list)
            
            for doc_id in filtered_documents:
                if doc_id not in doc_dict:
                    continue
                
                document = doc_dict[doc_id]
                
                # Determine collection name
                collection_name = "documents"
                if document.category_id:
                    cat = category_map.get(document.category_id)
                    if cat:
                        collection_name = cat.collection_name
                elif document.category:
                    collection_name = document.category.lower().replace(" ", "_").replace("-", "_")
                
                docs_by_collection[collection_name].append(doc_id)
            
            # Query each collection once
            for collection_name, doc_ids_in_collection in docs_by_collection.items():
                # Loop to prepare per-doc configs and calculate total n_results needed
                doc_limits = {}
                total_requested_k = 0
                
                for doc_id in doc_ids_in_collection:
                    document = doc_dict[doc_id]
                    
                    # Get per-document config
                    doc_config = doc_config_dict.get(doc_id, default_config)
                    top_k_chunks = doc_config.get("top_k_chunks_per_document", 10)
                    search_strategy = doc_config.get("search_strategy", "selective")
                    response_depth = doc_config.get("response_depth", "moderate")
                    
                    # Apply intelligent caps based on answer_mode and response_depth
                    # Start from the per-document AI decision, then tighten for safety.
                    if answer_mode == "extract":
                        top_k_chunks = min(top_k_chunks, 3)
                    elif answer_mode == "brief":
                        top_k_chunks = min(top_k_chunks, 4)
                    elif answer_mode == "summarize":
                        top_k_chunks = min(top_k_chunks, 5)
                    elif answer_mode == "explain":
                        if response_depth in ("deep", "exhaustive"):
                            top_k_chunks = min(top_k_chunks, 8)
                        elif response_depth == "moderate":
                            top_k_chunks = min(top_k_chunks, 5)
                        else:
                            top_k_chunks = min(top_k_chunks, 4)

                    # Overall safety bounds: never less than 2, never more than 8
                    top_k_chunks = max(2, min(8, int(top_k_chunks)))

                    print(f"  Doc {document.title[:30]}: {top_k_chunks} chunks (mode={answer_mode}, depth={response_depth})")
                    doc_limits[doc_id] = top_k_chunks
                    total_requested_k += top_k_chunks
                    
                    # Add to relevant_documents list
                    doc_idx = document_ids.index(doc_id) if doc_id in document_ids else 0
                    relevant_documents.append({
                        "document_id": doc_id,
                        "title": document.title,
                        "collection_name": collection_name,
                        "category": document.category or "documents",
                        "doc_type": doc_types.get(doc_id, "other"),
                        "description": document.description,
                        "score": float(master_results["distances"][0][doc_idx]) if master_results.get("distances") and doc_idx < len(master_results["distances"][0]) else 0.0,
                        "top_k_chunks": top_k_chunks,
                        "response_length": doc_config.get("response_length", "medium"),
                        "response_depth": doc_config.get("response_depth", "moderate"),
                        "estimated_tokens": doc_config.get("estimated_tokens", 3000)
                    })

                # Perform Batch Query
                # We request (total_top_k * 1.5) to ensure coverage, capped at 200
                query_limit_global = min(int(total_requested_k * 1.5) + 5, 200)
                
                try:
                    print(f"  Collection '{collection_name}': Batch query for {len(doc_ids_in_collection)} docs (limit={query_limit_global})")
                    chunk_results = query_collection_with_filter(
                        query_text=refined_question,
                        collection_name=collection_name,
                        n_results=query_limit_global,
                        where={"document_id": {"$in": doc_ids_in_collection}}
                    )

                    # Process results and distribute to documents
                    if chunk_results.get("ids") and chunk_results["ids"][0]:
                        chunks_found_map = defaultdict(int)
                        
                        for chunk_idx, chunk_id in enumerate(chunk_results["ids"][0]):
                            chunk_metadata = chunk_results["metadatas"][0][chunk_idx] if chunk_results.get("metadatas") else {}
                            chunk_doc_id = chunk_metadata.get("document_id")
                            
                            # Normalize ID
                            try:
                                if isinstance(chunk_doc_id, str):
                                    chunk_doc_id = int(chunk_doc_id)
                                elif chunk_doc_id is None:
                                    continue
                            except (ValueError, TypeError):
                                continue
                            
                            # Check if this chunk belongs to one of our requested docs
                            if chunk_doc_id in doc_ids_in_collection:
                                # Check per-document limit
                                if chunks_found_map[chunk_doc_id] < doc_limits.get(chunk_doc_id, 10):
                                    chunks_found_map[chunk_doc_id] += 1
                                    
                                    # Add chunk
                                    document = doc_dict[chunk_doc_id]
                                    source_chunks.append(SourceChunk(
                                        document_id=chunk_doc_id,
                                        document_title=document.title,
                                        category=document.category or "documents",
                                        chunk_text=chunk_results["documents"][0][chunk_idx] if chunk_results.get("documents") else "",
                                        page_number=chunk_metadata.get("page_number"),
                                        chunk_index=chunk_metadata.get("chunk_index"),
                                        score=float(chunk_results["distances"][0][chunk_idx]) if chunk_results.get("distances") else 0.0
                                    ))
                        
                        # Log distribution
                        # print(f"    Distribution: {dict(chunks_found_map)}")
                        
                except Exception as e:
                    print(f"  Error querying collection {collection_name}: {str(e)}")
                    continue
            
            step3_duration = time.time() - step3_start_time
            print(f"Step 3 Completed in {step3_duration:.2f}s")
        else:
            print("Step 3: No documents to retrieve (skipped)")
            relevant_documents = []
            source_chunks = []
        
        if not source_chunks and not relevant_documents:
            return AskResponse(
                answer="I couldn't find any relevant documents or chunks to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        print(f"Step 3 Results: Found {len(relevant_documents)} documents with {len(source_chunks)} chunks")
        # ====================================================================
        # STEP 3.5: ENFORCE GLOBAL CHUNK TOKEN BUDGET
        # Estimate reserved tokens for system/prompt/history/response and
        # compute how many chunks we can safely include based on a conservative
        # tokens-per-minute budget (30k). This protects against token overflows
        # before downstream processing.
        # ====================================================================
        try:
            print(">>> ENTERING STEP 3.5: token budget enforcement <<<")
            # Show pre-condition variables to confirm data flow
            pre_count = len(source_chunks) if 'source_chunks' in locals() else 0
            print(f"  Pre-step3.5: source_chunks={pre_count}, estimated_tokens_present={'estimated_tokens' in locals()}, conversation_history_present={'conversation_history' in locals()}")

            # Reserve tokens for prompt structure and system messages
            recent_history_count = len(conversation_history[-5:]) if 'conversation_history' in locals() else 0
            reserved_for_prompt = 3000
            reserved_for_history = recent_history_count * 200

            # Use estimated_tokens (desired response size) as reservation if available
            reserved_for_response = estimated_tokens if 'estimated_tokens' in locals() else 3000

            reserved_tokens = reserved_for_prompt + reserved_for_history + reserved_for_response

            # Chunk token budget from a conservative TPM limit (30,000)
            chunk_token_budget = 30000 - reserved_tokens
            avg_tokens_per_chunk = 800  # conservative average tokens per chunk

            max_chunks_allowed = max(5, int(chunk_token_budget / avg_tokens_per_chunk))

            print(f"Step 3.5 Token Budget: reserved={reserved_tokens}, budget_for_chunks={chunk_token_budget}, max_chunks_allowed={max_chunks_allowed}")

            if len(source_chunks) > max_chunks_allowed:
                print(f"  ⚠️ Token budget exceeded: trimming chunks {len(source_chunks)} → {max_chunks_allowed}")
                sorted_chunks = sorted(source_chunks, key=lambda c: c.score)
                source_chunks = sorted_chunks[:max_chunks_allowed]
                print(f"  [OK] Kept top {len(source_chunks)} chunks (scores {source_chunks[0].score:.3f} - {source_chunks[-1].score:.3f})")
            else:
                print(f"  No trimming needed: source_chunks={len(source_chunks)} <= max_chunks_allowed={max_chunks_allowed}")

            print(">>> EXITING STEP 3.5 <<<")
        except Exception as e:
            print(f"Step 3.5 token budget enforcement skipped due to error: {e}")
        
        # ================================================================
        # INTENT-AWARE CHUNK RE-RANKING - DISABLED
        # Restoring old behavior: Trust semantic search scores from ChromaDB
        # This fixes regression where re-ranking/trimming discarded relevant context
        # ================================================================
        # if source_chunks and len(source_chunks) > 3:
        #     _stop = {"what", "is", "the", "a", "an", "in", "for", "of", "to", "and",
        #              "or", "how", "does", "do", "are", "was", "were", "be", "been",
        #              "type", "kind", "about", "tell", "me", "can", "you",
        #              "which", "where", "when", "who", "why", "this", "that",
        #              "these", "those", "it", "its", "s", "have", "has", "had"}
        #     intent_keywords = [
        #         w.lower().strip("?.,!\"'")
        #         for w in refined_question.split()
        #         if w.lower().strip("?.,!\"'") not in _stop and len(w) > 2
        #     ]
        #     if primary_entity:
        #         intent_keywords.insert(0, primary_entity.lower())
        #     
        #     if intent_keywords:
        #         print(f"  Intent re-ranking keywords: {intent_keywords[:8]}")
        #         
        #         scored = []
        #         for idx, chunk in enumerate(source_chunks):
        #             ct = (chunk.chunk_text or "").lower()
        #             iscore = sum(1 for kw in intent_keywords if kw in ct)
        #             # Entity match in chunk gets strong bonus
        #             if primary_entity and primary_entity.lower() in ct:
        #                 iscore += 3
        #             scored.append((iscore, chunk.score, idx, chunk))
        #         
        #         # Sort: highest intent score first, then lowest distance
        #         scored.sort(key=lambda x: (-x[0], x[1]))
        #         source_chunks = [s[3] for s in scored]
        #         
        #         # Log top 3 after re-ranking
        #         for i in range(min(3, len(scored))):
        #             sc = scored[i]
        #             print(f"  Re-ranked #{i+1}: intent={sc[0]}, "
        #                   f"dist={sc[1]:.3f}, doc='{sc[3].document_title[:50]}'")
        #         
        #         # Trim excess chunks for factual question types
        #         pre_trim = len(source_chunks)
        #         if answer_mode in ("extract", "brief") and len(source_chunks) > 8:
        #             source_chunks = source_chunks[:8]
        #         elif answer_mode == "summarize" and len(source_chunks) > 12:
        #             source_chunks = source_chunks[:12]
        #         if len(source_chunks) < pre_trim:
        #             print(f"  Trimmed chunks: {pre_trim} → {len(source_chunks)} ({answer_mode} mode)")
        
        # ====================================================================
        # STEP 3 DATA QUALITY ASSESSMENT: Evaluate locally (no API call)
        # ====================================================================
        # Evaluate data quality locally based on retrieved chunks
        if source_chunks and relevant_documents:
            avg_chunks_per_doc = len(source_chunks) / len(relevant_documents)
            
            # OLD: Count-based quality assessment (keeping for baseline)
            # if avg_chunks_per_doc >= 5 and len(source_chunks) >= 10:
            #     data_quality = "excellent"
            #     confidence_score = 85
            # elif avg_chunks_per_doc >= 3 and len(source_chunks) >= 5:
            #     data_quality = "good"
            #     confidence_score = 75
            # elif avg_chunks_per_doc >= 1 and len(source_chunks) >= 3:
            #     data_quality = "sufficient"
            #     confidence_score = 65
            # else:
            #     data_quality = "insufficient"
            #     confidence_score = 50
            
            # NEW: Combined count + relevance-based quality assessment
            # Step 1: Base assessment from count
            if avg_chunks_per_doc >= 5 and len(source_chunks) >= 10:
                data_quality = "excellent"
                confidence_score = 85
            elif avg_chunks_per_doc >= 3 and len(source_chunks) >= 5:
                data_quality = "good"
                confidence_score = 75
            elif avg_chunks_per_doc >= 1 and len(source_chunks) >= 3:
                data_quality = "sufficient"
                confidence_score = 65
            else:
                data_quality = "insufficient"
                confidence_score = 50
            
            # Step 2: Adjust based on relevance (ChromaDB distance: lower = better)
            avg_distance = sum(chunk.score for chunk in source_chunks) / len(source_chunks)
            
            if avg_distance < 0.3:
                relevance_quality = "high"
                confidence_score = min(95, confidence_score + 5)
            elif avg_distance < 0.5:
                relevance_quality = "medium"
                # No adjustment
            elif avg_distance < 0.7:
                relevance_quality = "low"
                confidence_score = max(50, confidence_score - 15)
                if data_quality == "excellent":
                    data_quality = "good"
            else:
                relevance_quality = "very_low"
                confidence_score = max(40, confidence_score - 30)
                if data_quality in ["excellent", "good"]:
                    data_quality = "sufficient"
            
            print(f"Step 3 Data Quality Assessment:")
            print(f"  Chunks: {len(source_chunks)}, Avg per doc: {avg_chunks_per_doc:.1f}")
            print(f"  Relevance: avg_distance={avg_distance:.3f} ({relevance_quality})")
            print(f"  Final: quality={data_quality}, confidence={confidence_score}")
        else:
            data_quality = "insufficient"
            confidence_score = 30
            print(f"Step 3 Data Quality Assessment: No chunks or documents found")
        
        step3_decision = {
            "proceed_to_step4": True,
            "data_quality": data_quality,
            "confidence_score": confidence_score,
            "relevance_quality": relevance_quality if 'relevance_quality' in locals() else "medium",
            "avg_chunk_distance": avg_distance if 'avg_chunk_distance' in locals() else 0.5
        }
        
        # Step 3: Choose model selection based on documents and question complexity
        # Aggregate response requirements from all documents
        all_response_lengths = [doc.get("response_length", "medium") for doc in relevant_documents]
        all_response_depths = [doc.get("response_depth", "moderate") for doc in relevant_documents]
        all_estimated_tokens = [doc.get("estimated_tokens", 3000) for doc in relevant_documents]
        
        # Determine overall response characteristics (use most demanding)
        length_priority = {"comprehensive": 4, "detailed": 3, "medium": 2, "brief": 1}
        depth_priority = {"exhaustive": 4, "deep": 3, "moderate": 2, "high-level": 1}
        
        response_length = max(all_response_lengths, key=lambda x: length_priority.get(x, 2))
        response_depth = max(all_response_depths, key=lambda x: depth_priority.get(x, 2))
        estimated_tokens = max(all_estimated_tokens) if all_estimated_tokens else 3000
        estimated_tokens = max(500, min(10000, estimated_tokens))
        
        # Adjust response parameters based on data quality from Step 3 decision
        if step3_decision.get("data_quality") == "insufficient":
            # Reduce response length/depth if data quality is insufficient
            if length_priority.get(response_length, 2) > 2:
                response_length = "medium"
                print(f"  Adjusted response_length to 'medium' due to insufficient data quality")
            if depth_priority.get(response_depth, 2) > 2:
                response_depth = "moderate"
                print(f"  Adjusted response_depth to 'moderate' due to insufficient data quality")
            estimated_tokens = min(estimated_tokens, 2000)  # Cap tokens for insufficient data
        
        # ====================================================================
        # GLOBAL CHUNK CAP: Ensure total chunks don't exceed safe limits
        # Safety mechanism even after per-document limiting
        if response_depth in ("deep", "exhaustive") or response_length == "comprehensive":
            max_total_chunks = 40
        elif response_depth == "moderate" or response_length in ("detailed", "medium"):
            max_total_chunks = 30
        else:
            max_total_chunks = 25

        print(f"Global Chunk Cap: max_total={max_total_chunks}, current={len(source_chunks)}")

        if len(source_chunks) > max_total_chunks:
            print(f"  ⚠️ Trimming chunks: {len(source_chunks)} → {max_total_chunks}")
            sorted_chunks = sorted(source_chunks, key=lambda c: c.score)
            source_chunks = sorted_chunks[:max_total_chunks]
            print(f"  [OK] Kept top {max_total_chunks} chunks (relevance: {source_chunks[0].score:.3f} - {source_chunks[-1].score:.3f})")

        # DYNAMIC MODEL SELECTION (Enhanced 6-Factor Algorithm)
        # Intelligently select model based on multiple factors
        # ====================================================================
        # Base model selection on complexity - 6-factor scoring system
        base_model_score = 0
        model_score_breakdown = {}
        
        # Factor 1: Response requirements (HIGHEST PRIORITY)
        factor1_score = 0
        if length_priority.get(response_length, 2) >= 4:  # comprehensive
            factor1_score = 2
        elif length_priority.get(response_length, 2) >= 3:  # detailed
            factor1_score = 1
        base_model_score += factor1_score
        model_score_breakdown["response_length"] = (factor1_score, response_length)
        
        # Factor 2: Response depth requirements (HIGHEST PRIORITY)
        factor2_score = 0
        if depth_priority.get(response_depth, 2) >= 4:  # exhaustive
            factor2_score = 2
        elif depth_priority.get(response_depth, 2) >= 3:  # deep
            factor2_score = 1
        base_model_score += factor2_score
        model_score_breakdown["response_depth"] = (factor2_score, response_depth)
        
        # Factor 3: Token requirements
        factor3_score = 0
        if estimated_tokens > 6000:
            factor3_score = 2
        elif estimated_tokens > 3000:
            factor3_score = 1
        base_model_score += factor3_score
        model_score_breakdown["token_requirements"] = (factor3_score, f"{estimated_tokens} tokens")
        
        # Factor 4: Query complexity & length (complex discourse needs better model)
        factor4_score = 0
        if has_complex_question and query_length > 100:
            factor4_score = 1
        base_model_score += factor4_score
        model_score_breakdown["query_complexity"] = (factor4_score, "complex" if factor4_score > 0 else "simple")
        
        # Factor 5: Conversation context (multi-turn conversations need coherence)
        factor5_score = 0
        if is_follow_up and conversation_length > 2:
            factor5_score = 1
        base_model_score += factor5_score
        model_score_breakdown["conversation_context"] = (factor5_score, f"{conversation_length} messages" if factor5_score > 0 else "first query")
        
        # Factor 6: Data quality (insufficient/low relevance needs better reasoning)
        factor6_score = 0
        data_quality_val = step3_decision.get("data_quality", "sufficient")
        if data_quality_val == "insufficient":
            factor6_score = 2  # Need GPT-4o to reason through sparse data
        elif data_quality_val in ("low", "very_low"):
            factor6_score = 1  # Need stronger model for weak data
        base_model_score += factor6_score
        model_score_breakdown["data_quality"] = (factor6_score, data_quality_val)
        
        # Bonus: Number of documents (more docs = more synthesis needed)
        bonus_score = 0
        if len(relevant_documents) > 5:
            bonus_score = 1
        base_model_score += bonus_score
        model_score_breakdown["document_count"] = (bonus_score, f"{len(relevant_documents)} docs" if bonus_score > 0 else "single/few docs")
        
        # User preference override
        if request.model_preference:
            selected_model = request.model_preference
            print(f"Using user-specified model: {selected_model}")
        elif base_model_score >= 5:
            selected_model = "gpt-4o"  # Use GPT-4o for complex/comprehensive/insufficient-data queries
        elif base_model_score >= 3:
            selected_model = "gpt-4o"  # Use GPT-4o for moderate-to-high complexity
        else:
            selected_model = "gpt-4o-mini"  # Use GPT-4o-mini for simple queries
        
        print(f"Step 3 Dynamic Model Selection Analysis:")
        print(f"  ┌─ Factor Breakdown:")
        for factor_name, (score, detail) in model_score_breakdown.items():
            print(f"  ├─ {factor_name}: +{score} ({detail})")
        print(f"  └─ TOTAL SCORE: {base_model_score} → Selected Model: {selected_model}")
        print(f"Step 3 Model Selection: {selected_model} (score={base_model_score}, length={response_length}, depth={response_depth}, tokens={estimated_tokens})")
        
        # ====================================================================
        # DYNAMIC MAX TOKENS CALCULATION
        # Intelligently calculate max_tokens based on multiple factors
        # ====================================================================
        # Base token calculation
        base_max_tokens = estimated_tokens
        
        # Factor 1: Model capabilities
        if selected_model == "gpt-4o":
            # GPT-4o can handle longer responses, allow more headroom
            base_max_tokens = int(base_max_tokens * 1.2)
        else:
            # GPT-4o-mini, keep closer to estimate
            base_max_tokens = int(base_max_tokens * 1.1)
        
        # Factor 2: Conversation context (follow-ups may need more context)
        if is_follow_up:
            base_max_tokens = int(base_max_tokens * 1.15)  # 15% more for context
        
        # Factor 3: Response length requirements
        if response_length == "comprehensive":
            base_max_tokens = int(base_max_tokens * 1.3)
        elif response_length == "detailed":
            base_max_tokens = int(base_max_tokens * 1.2)
        elif response_length == "brief":
            base_max_tokens = int(base_max_tokens * 0.8)
        
        # Factor 4: Number of documents (more docs = potentially longer answer)
        if len(relevant_documents) > 3:
            base_max_tokens = int(base_max_tokens * 1.1)
        
        # Factor 5: Data quality (insufficient data = shorter answer)
        if step3_decision.get("data_quality") == "insufficient":
            base_max_tokens = int(base_max_tokens * 0.7)
        
        # Apply bounds
        min_tokens = 500
        max_tokens_limit = 16000 if selected_model == "gpt-4o" else 16000  # Both models support up to 16k
        dynamic_max_tokens = max(min_tokens, min(max_tokens_limit, base_max_tokens))
        
        # User override
        if request.max_tokens:
            dynamic_max_tokens = max(min_tokens, min(max_tokens_limit, request.max_tokens))
            print(f"Using user-specified max_tokens: {dynamic_max_tokens}")
        
        print(f"Dynamic Max Tokens: {dynamic_max_tokens} (base={estimated_tokens}, adjusted for model={selected_model}, context={is_follow_up}, length={response_length})")
        
        # ====================================================================
        # STEP 4: Generate final answer
        # ====================================================================
        print(f"Step 4: Generating final answer...")
        step4_start_time = time.time()
        
        # Build document summaries and chunks in JSON format
        document_summaries = []
        for doc in relevant_documents:
                document_summaries.append({
                    "document_id": doc["document_id"],
                    "title": doc["title"],
                "collection_name": doc["collection_name"],
                    "category": doc.get("category"),
                "description": doc.get("description", ""),
                    "relevance_score": doc.get("score", 0.0)
                })
        
        chunks_json = []
        for chunk in source_chunks:
            chunks_json.append({
                "document_id": chunk.document_id,
                "document_title": chunk.document_title,
                "collection_name": chunk.category,
                "chunk_text": chunk.chunk_text,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "relevance_score": chunk.score
            })


                # DEBUG: Print retrieved chunks to verify correct data
        #print(f"DEBUG - Retrieved {len(chunks_json)} chunks for question: {refined_question[:50]}...")
        #for i, chunk in enumerate(chunks_json[:3]):  # Show first 3 chunks
            #chunk_preview = chunk.get('chunk_text', '')[:400].replace('\n', ' ')
            #print(f"CHUNK {i+1} ({chunk.get('document_title', 'Unknown')}): {chunk_preview}...")
        
        # ================================================================
        # DEBUG: COMPREHENSIVE LOGGING FOR DEBUGGING RAG RETRIEVAL
        # ================================================================
        print("\n" + "="*80)
        print("DEBUG - RETRIEVED DATA SUMMARY")
        print("="*80)
        print(f"Question: {refined_question}")
        print(f"Total Chunks Retrieved: {len(chunks_json)}")
        print(f"Total Documents: {len(document_summaries)}")
        
        # Print document summaries with descriptions
        print("\n" + "-"*80)
        print("DOCUMENT SUMMARIES (with descriptions):")
        print("-"*80)
        for i, doc_summary in enumerate(document_summaries):
            print(f"\n[DOC {i+1}] {doc_summary.get('title', 'Unknown')}")
            print(f"  Document ID: {doc_summary.get('document_id')}")
            print(f"  Collection: {doc_summary.get('collection_name', 'N/A')}")
            print(f"  Category: {doc_summary.get('category', 'N/A')}")
            print(f"  Relevance Score: {doc_summary.get('relevance_score', 0):.4f}")
            desc = doc_summary.get('description', 'No description')
            print(f"  Description: {desc[:500] if desc else 'No description'}{'...' if desc and len(desc) > 500 else ''}")
        
        # Print ALL chunks with full content
        print("\n" + "-"*80)
        print("ALL RETRIEVED CHUNKS (full content):")
        print("-"*80)
        for i, chunk in enumerate(chunks_json):
            print(f"\n{'='*60}")
            print(f"CHUNK {i+1}/{len(chunks_json)}")
            print(f"{'='*60}")
            print(f"  Document: {chunk.get('document_title', 'Unknown')}")
            print(f"  Document ID: {chunk.get('document_id')}")
            print(f"  Collection: {chunk.get('collection_name', 'N/A')}")
            print(f"  Page Number: {chunk.get('page_number', 'N/A')}")
            print(f"  Chunk Index: {chunk.get('chunk_index', 'N/A')}")
            print(f"  Relevance Score: {chunk.get('relevance_score', 0):.4f}")
            print(f"  --- FULL CHUNK TEXT ---")
            chunk_text = chunk.get('chunk_text', '')
            # Print full chunk text (with line breaks preserved)
            print(chunk_text)
            print(f"  --- END CHUNK TEXT ({len(chunk_text)} chars) ---")
        
        print("\n" + "="*80)
        print("END DEBUG - RETRIEVED DATA SUMMARY")
        print("="*80 + "\n")
        
        # Convert to TOON format for Step 4
        summaries_toon_str, step4_summaries_json, step4_summaries_toon = convert_json_to_toon_and_show_savings(
            document_summaries, "Step 4: Final Answer Generation", "Document Summaries"
        )
        
        if chunks_json:
            chunks_toon_str, step4_chunks_json, step4_chunks_toon = convert_json_to_toon_and_show_savings(
                chunks_json, "Step 4: Final Answer Generation", "Chunks"
            )
        else:
            chunks_toon_str = encode([])
            if isinstance(chunks_toon_str, bytes):
                chunks_toon_str = chunks_toon_str.decode('utf-8')
            elif not isinstance(chunks_toon_str, str):
                chunks_toon_str = str(chunks_toon_str)
            step4_chunks_json = len(enc.encode("[]"))
            step4_chunks_toon = len(enc.encode(chunks_toon_str))
        
        # Phase 1 Refactor: Determine role/response_type and build response instructions
        role = select_role(intent, intent_hints)
        response_type = select_response_type(intent, role, refined_question, intent_hints)
        constraints = build_constraints(role, response_type)

        # Build response instructions
        length_instructions = {
            "brief": "Give a complete answer in 2-3 sentences. Cover all key facts, no filler.",
            "medium": "Give a complete answer in 4-6 sentences. Use bullet points if multiple facts.",
            "detailed": "Give a structured answer with brief intro, key points as bullets, and conclusion. Keep paragraphs short.",
            "comprehensive": "Cover all aspects using clear sections with bullet points. Be thorough but avoid repetition."
        }
        
        depth_instructions = {
            "high-level": "Focus on overview, summaries, and high-level concepts. Avoid deep technical details.",
            "moderate": "Provide balanced detail with context and explanations. Include some specifics but maintain readability.",
            "deep": "Provide in-depth analysis with detailed explanations, examples, and thorough coverage of the topic.",
            "exhaustive": "Provide exhaustive coverage with all relevant details, comprehensive analysis, and complete information."
        }
        
        length_instruction = length_instructions.get(response_length, length_instructions["medium"])
        depth_instruction = depth_instructions.get(response_depth, depth_instructions["moderate"])
        
        chunks_note = ""
        if not chunks_json:
            chunks_note = "\nNOTE: No detailed chunks are available for this query. Use the Document Summaries to answer the question."
        
        # Build Step 4 prompt with TOON format (includes data quality awareness)
        # Get data quality from step3_decision (defined earlier)
        data_quality = step3_decision.get("data_quality", "sufficient")
        confidence_score = step3_decision.get("confidence_score", 70)
        
        # ================================================================
        # STEP 4 PROMPT - RESTRUCTURED (Senior Prompt Engineer Design)
        # Principles: Mode-First, Critical Rules at Top, Intelligent Adaptation
        # OLD PROMPT COMMENTED OUT BELOW (lines were 1665-1889)
        # ================================================================
        
        # Build data quality context with enhanced relevance information
        quality_context = f"[{len(source_chunks)} chunks from {len(relevant_documents)} doc(s), confidence: {confidence_score}%]"
        
        # Add relevance quality assessment to context
        relevance_quality = step3_decision.get("relevance_quality", "medium")
        avg_chunk_distance = step3_decision.get("avg_chunk_distance", 0.5)
        
        relevance_hint = ""
        if relevance_quality == "high":
            relevance_hint = "[OK] Excellent chunk relevance - chunks are highly relevant to the question"
        elif relevance_quality == "medium":
            relevance_hint = "● Moderate chunk relevance - chunks are reasonably relevant"
        elif relevance_quality == "low":
            relevance_hint = "▼ Low chunk relevance - chunks may have mixed relevance"
        else:  # very_low
            relevance_hint = "[WARN] Very low chunk relevance - be cautious about answer confidence"
        
        if data_quality == "insufficient":
            quality_warning = "\n⚠️ LIMITED DATA: The retrieved data is limited. Acknowledge information gaps if they exist."
        elif data_quality == "excellent":
            quality_warning = "\n[OK] RICH DATA: You have comprehensive data from quality sources. Provide detailed answer."
        elif data_quality == "good":
            quality_warning = "\n● SOLID DATA: You have good coverage across relevant documents."
        else:  # sufficient or lower
            quality_warning = "\n▼ ADEQUATE DATA: Work with available information; note any limitations transparently."
        
        if relevance_hint:
            quality_warning += f"\n{relevance_hint}"
        
        # Determine sales vs deep mode from question heuristics
        _q_low = (refined_question or "").lower()
        sales_mode = True
        deep_triggers = ["technical", "architecture", "how exactly", "integration", "api", "implementation details", "design"]
        for t in deep_triggers:
            if t in _q_low:
                sales_mode = False
                break

        # Extract core fear to focus answer on primary user concern (Option C)
        core_fear = infer_core_fear(refined_question)

        step4_prompt = f"""You are an intelligent document assistant. Answer ONLY from the provided chunks.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 🚨 ABSOLUTE RULES (Violating these = FAILURE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. ONLY use information from the chunks below - NO external knowledge unless user explicitly asks for an opinion
2. NEVER mix information from different client proposals  
3. If not in chunks → "This is not explicitly stated in the documents" (but still provide a concise recommendation if in Sales Mode)
4. Cite document titles only - NEVER page numbers. Use format: "Source: [Document Title]"
5. Before saying "not found" → scan for bullet points, tables, lists
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

""" + build_prompt_header(answer_mode, role, response_type, core_fear)

        # Add behavioral directives (first-pass guidance from delta checklist)
        step4_prompt += "\n## BEHAVIORAL DIRECTIVES\n" + build_behavioral_directives(role, response_type, core_fear) + "\n\n"

        # Add mode-specific instructions (this is where intelligence happens)
        if answer_mode == "extract":
            step4_prompt += """
**EXTRACTION MODE** - Provide the specific value in a clear, conversational sentence.

FORMAT: Write a complete, friendly sentence that answers the question directly.

RULES:
- Search chunks for the requested information
- Present findings in a professional, readable format
- If multiple items found, list them clearly with context
- If truly not found: "I couldn't find specific information about [topic] in the available documents."

GOOD: "The tools integrated into the CI/CD pipeline for security and compliance include Polaris for static code analysis, Black Duck for software composition analysis, and HashiCorp Vault for secrets management."
BAD: "The document lists: [Polaris, Black Duck]"
"""
        elif answer_mode == "brief":
            step4_prompt += """
**BRIEF MODE** - Yes/No + One sentence of evidence.

FORMAT: 
[Yes/No], [one sentence with document evidence]

RULES:
• First word must be "Yes" or "No" (or "Partially" if mixed)
• ONE supporting sentence citing the document
• NO bullet points, NO headers, NO elaboration

GOOD: "Yes, HashiCorp Vault is included for secrets management according to the Keysight DevOps Proposal."
BAD: "Based on my analysis of the documents, I can confirm that..." (too wordy)
"""
        elif answer_mode == "summarize":
            step4_prompt += """
**SUMMARY MODE** - Key facts as concise bullet points.

FORMAT:
• [Fact 1]
• [Fact 2]  
• [Fact 3-5]
**Source:** [Document Title]

RULES:
• 3-5 bullets maximum
• Each bullet = one distinct fact (no repetition)
• No fluff words ("importantly", "notably", "interestingly")
• End with source citation
"""
        else:  # explain mode (default)
            step4_prompt += f"""
**EXPLANATION MODE** - Intelligent, structured response.

RESPONSE REQUIREMENTS (Response Length: {response_length.upper()}, Depth: {response_depth.upper()}):
• Simple factual question → 2-3 sentences (brief/moderate)
• "How does X work?" → 1-2 paragraphs with structure (medium/deep)
• "Compare X and Y" → Structured comparison with headers (detailed/deep)
• "Explain the approach" → Comprehensive with sections (comprehensive/exhaustive)

LENGTH GUIDANCE:
• Brief (1-3 sentences): For straightforward topics, definitional questions
• Medium (2-4 paragraphs): For standard questions with context needs
• Detailed (4-6 paragraphs): For questions needing thorough explanation and structure
• Comprehensive (6+ paragraphs with sections): For complex topics requiring exhaustive coverage

DEPTH GUIDANCE:
• High-level: Focus on overview, summaries, conceptual understanding
• Moderate: Provide balanced detail with context and key specifics
• Deep: Provide in-depth analysis with detailed explanations and examples
• Exhaustive: Provide exhaustive coverage with all relevant details, comprehensive analysis, and complete information

RESPONSE STRUCTURE (adapt to depth level):
• Shallow: Single paragraph with key facts
• Moderate: Intro + Body + Conclusion
• Deep: Intro + Categories/Sections with Examples + Analysis + Conclusion
• Exhaustive: Multiple sections with detailed analysis, comparisons, implications

RULES:
• Lead with the direct answer, then support with evidence
• Use headers/bullets for complex multi-part answers
• For deep/exhaustive: Include comparative analysis, examples, and comprehensive coverage
• In Sales Mode: Use the structure: Recommendation → Why → How → Proof (short, persuasive)
• In Sales Mode: prefer phrasing like "Recommendation: ...", use experience framing ("We have seen...", "Typically..."), and end with a "Source:" line (humanized titles only)
• NEVER use phrases like "According to" or "The document says". Do not output internal filenames.
• In Deep Mode: include technical citations and implementation notes
• End with **Source:** if referencing multiple documents
"""

        # Add the context and question (via centralized builder)
        step4_prompt += build_context_blocks(
            summaries_toon_str, chunks_toon_str, refined_question, data_quality, quality_context, quality_warning
        )

        step4_prompt += """
    ## 🧠 THINK BEFORE ANSWERING:
1. What specific information does the user need?
2. Which chunks contain the answer?
3. Is this a single-fact or multi-part question?
4. What's the minimum response that fully answers this?

## 🔴 MANDATORY VERIFICATION BEFORE ANY REFUSAL

If you are about to say "not stated", "not found", "not mentioned", "not explicitly", or similar:

**STOP. Complete this forced scan first:**

1. **SCAN FOR BULLETS**: Look in chunks for: •, -, *, →, ▶
2. **SCAN FOR NUMBERS**: Look for: 1., 2., 3., (1), (a), (b), i., ii.
3. **SCAN FOR KEYWORDS**: "includes", "scope", "covers", "validates", "features", "comprises"
4. **SCAN FOR TABLES**: Look for aligned columns or | characters

**EXTRACTION RULE:**
- IF structured data found → Extract and list it verbatim
- DO NOT refuse when lists/bullets exist in chunks
- Enumerate findings: "The document lists: [item1], [item2], [item3]"

**ONLY refuse if ALL 4 scans found NOTHING relevant.**

        # Build a short Proof snippet from retrieved chunks for Sales Mode (guarded)
        try:
            proof_snippet = build_proof_snippet(chunks_json)
        except Exception:
            proof_snippet = None
        if 'sales_mode' in locals() and sales_mode and proof_snippet:
            step4_prompt += "\n## PROOF (Extracted Evidence)\n" + proof_snippet + "\n"

If refusing, state: "After scanning all chunks for lists, bullets, and structured data related to [topic], I could not find specific information."


## ✍️ YOUR ANSWER:
"""

        # Track Step 4 prompt tokens
        step4_prompt_tokens = len(enc.encode(step4_prompt))
        
        # Build concise system message (restructured for clarity)
        # Default persona: Senior Pre-Sales Consultant (Sales Mode)
        system_message = """You are a Senior Pre-Sales Consultant. Default to Sales Mode: be concise, persuasive, and outcome-focused.
    - Recommendation → Why → How → Proof is the required response structure in Sales Mode.
    - Use documents strictly for proof; include a final 'Source:' line with human-friendly titles. NEVER say "According to..." or "The document says"..."
    - NEVER cite internal filenames (underscores, raw file names). Always humanize titles (e.g., "BugBuster_Solutions" → "BugBuster").
    - If Deep Mode was triggered by the question, switch to a technical consultant persona and provide implementation-level details.

    Behavioral rules:
    - Accurate: Base claims on provided chunks; avoid speculation.
    - Experience framing: Prefer phrasing like "We have seen...", "In our experience...", "Typically..."
    - Adaptive: Match response length to question complexity and mode.
    - Structured: Use clear headers and bullets where helpful.
    - Honest: If information is missing, state limits clearly but still provide a concise recommendation in Sales Mode when possible.

    Never reveal financial data. Always cite document titles, never page numbers."""
        
        if is_follow_up:
            system_message += " This is a follow-up question - maintain context continuity."
        
        if is_clarification:
            system_message += " User is asking for clarification - be more detailed and clear."
        
        # Build messages array with conversation history
        messages = [
                {
                    "role": "system",
                "content": system_message
            }
        ]
        
        # Add conversation history (last 5 messages to avoid token bloat)
        if conversation_history:
            for msg in conversation_history[-5:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ["user", "assistant"] and content:
                    messages.append({
                        "role": role,
                        "content": content
                    })
        
        # Add current prompt
        messages.append({
            "role": "user",
            "content": step4_prompt
        })
        
        # Call OpenAI for final answer with dynamic settings optimized for intelligence
        # Adjust temperature based on query complexity for optimal reasoning
        if has_complex_question or is_clarification:
            reasoning_temperature = 0.5  # Lower temperature for more precise reasoning on complex queries
        elif is_follow_up:
            reasoning_temperature = 0.6  # Slightly lower for better context coherence
        else:
            reasoning_temperature = 0.7  # Standard temperature for conversational responses
        
        print(f"Using model: {selected_model} with max_tokens: {dynamic_max_tokens}, temperature: {reasoning_temperature} (conversational={is_follow_up}, complex={has_complex_question})")
        step4_response = client.chat.completions.create(
            model=selected_model,
            messages=messages,
            max_tokens=dynamic_max_tokens,
            temperature=reasoning_temperature  # Optimized for intelligent reasoning
        )
        print(f"Step 4 API Call Completed in {time.time() - step4_start_time:.2f}s")
        
        # Track Step 4 response tokens
        step4_response_tokens = step4_response.usage.total_tokens if hasattr(step4_response, 'usage') else 0
        step4_total_json = step4_prompt_tokens + step4_response_tokens + step4_summaries_json + step4_chunks_json
        step4_total_toon = step4_prompt_tokens + step4_response_tokens + step4_summaries_toon + step4_chunks_toon
        step4_savings = step4_total_json - step4_total_toon
        step4_savings_percent = (step4_savings / step4_total_json * 100) if step4_total_json > 0 else 0
        
        # Track Step 4
        token_usage_tracker["calls"].append({
            "call_name": "Step 4: Final Answer Generation",
            "json_tokens": step4_total_json,
            "toon_tokens": step4_total_toon,
            "savings": step4_savings,
            "savings_percent": step4_savings_percent
        })
        
        # Extract answer
        answer = step4_response.choices[0].message.content.strip()
        
        # ====================================================================
        # STEP 4 DECISION: Evaluate answer quality and potentially refine
        # Based on data quality from Step 3 and answer completeness
        # ====================================================================
        data_quality = step3_decision.get("data_quality", "sufficient")
        confidence_score = step3_decision.get("confidence_score", 70)
        relevance_quality = step3_decision.get("relevance_quality", "medium")
        avg_chunk_distance = step3_decision.get("avg_chunk_distance", 0.5)
        
        print(f"Step 4 Complete: Answer generated with {len(answer)} characters")
        print(f"  Data Quality: {data_quality}")
        print(f"  Relevance Quality: {relevance_quality} (avg_distance={avg_chunk_distance:.3f})")
        print(f"  Confidence Score: {confidence_score}/100")
        print(f"  Model Used: {selected_model}")
        print(f"  Response Settings: length={response_length}, depth={response_depth}")
        
        # Store Step 4 response with complete information
        step4_response_data = {
            "call_name": "Step 4: Final Answer Generation",
            "request_prompt": step4_prompt,  # Store full prompt (will be truncated in JSON if needed)
            "response_content": answer,  # Store full answer (will be truncated in JSON if needed)
            "model_used": selected_model,
            "tokens_used": step4_total_toon,
            "tokens_without_toon": step4_total_json,
            "savings": step4_savings,
            "savings_percent": step4_savings_percent,
            "max_tokens": dynamic_max_tokens,
            "temperature": reasoning_temperature if 'reasoning_temperature' in locals() else 0.7,
            "response_length": response_length,
            "response_depth": response_depth,
            "answer_mode": answer_mode,
            "data_quality": step3_decision.get("data_quality", "sufficient"),
            "relevance_quality": step3_decision.get("relevance_quality", "medium"),
            "avg_chunk_distance": step3_decision.get("avg_chunk_distance", 0.5),
            "confidence_score": step3_decision.get("confidence_score", 70),
            "conversation_history_length": conversation_length,
            "is_follow_up": is_follow_up,
            "is_clarification": is_clarification,
            "model_selection_score": base_model_score if 'base_model_score' in locals() else 0,
            "model_selection_breakdown": model_score_breakdown if 'model_score_breakdown' in locals() else {},
            "num_documents_retrieved": len(relevant_documents),
            "num_chunks_retrieved": len(source_chunks),
            "dynamic_max_tokens_calculation": {
                "base_estimate": estimated_tokens,
                "model_adjustment": selected_model,
                "conversation_adjustment": is_follow_up,
                "response_length_adjustment": response_length,
                "final_max_tokens": dynamic_max_tokens
            }
        }
        api_call_responses.append(step4_response_data)
        
        # Calculate totals
        token_usage_tracker["total_json_tokens"] = sum(call["json_tokens"] for call in token_usage_tracker["calls"])
        token_usage_tracker["total_toon_tokens"] = sum(call["toon_tokens"] for call in token_usage_tracker["calls"])
        token_usage_tracker["total_savings"] = token_usage_tracker["total_json_tokens"] - token_usage_tracker["total_toon_tokens"]
        token_usage_tracker["total_savings_percent"] = (
            (token_usage_tracker["total_savings"] / token_usage_tracker["total_json_tokens"] * 100)
            if token_usage_tracker["total_json_tokens"] > 0 else 0.0
        )
        
        # Self-evaluation rubric (pre-return) and one-shot refinement gate
        rubric = evaluate_answer_simple(answer, role, response_type)
        print(f"Rubric: score={rubric['score']}, failed={rubric['failed']}")
        # Threshold: require at least basic structure & bans respected
        needs_refine = False
        if response_type == RESPONSE_TYPES.get("SALES_RECOMMENDATION"):
            must_have = {"no_banned_phrases", "has_source_line", "has_recommendation", "has_why", "has_how"}
            missing = [c for c in must_have if not rubric["checks"].get(c, False)]
            # If missing 2+ critical checks, attempt a refinement pass
            needs_refine = len(missing) >= 2
        else:
            # For other response types, ensure at least bans and source
            needs_refine = not (rubric["checks"].get("no_banned_phrases", True) and rubric["checks"].get("has_source_line", True))

        if needs_refine:
            print("Rubric gate: triggering one-shot refinement")
            refinement_instruction = build_refinement_instruction(rubric["failed"], role, response_type)
            refinement_messages = list(messages)
            refinement_messages.append({"role": "assistant", "content": answer})
            refinement_messages.append({"role": "user", "content": refinement_instruction})

            refine_max_tokens = max(512, min(dynamic_max_tokens, 2048))
            refine_temp = 0.5
            refine_response = client.chat.completions.create(
                model=selected_model,
                messages=refinement_messages,
                max_tokens=refine_max_tokens,
                temperature=refine_temp
            )
            refined_answer = refine_response.choices[0].message.content.strip()
            # Track refinement call
            refine_tokens = refine_response.usage.total_tokens if hasattr(refine_response, 'usage') else 0
            token_usage_tracker["calls"].append({
                "call_name": "Step 4b: Refinement (Rubric Gate)",
                "json_tokens": refine_tokens,  # approximate; TOON not applied for single instruction
                "toon_tokens": refine_tokens,
                "savings": 0,
                "savings_percent": 0.0
            })
            api_call_responses.append({
                "call_name": "Step 4b: Refinement (Rubric Gate)",
                "request_prompt": refinement_instruction,
                "response_content": refined_answer,
                "model_used": selected_model,
                "tokens_used": refine_tokens
            })
            answer = refined_answer

        # Convert answer to Slack message format (after possible refinement)
        slack_formatted_answer = format_answer_for_slack(answer)
        
        # Prepare TOON savings breakdown
        toon_savings_breakdown = {
            "by_call": token_usage_tracker["calls"],
            "total_savings": token_usage_tracker["total_savings"],
            "total_savings_percent": round(token_usage_tracker["total_savings_percent"], 2)
        }
        
        # Prepare token usage summary (with optimization metadata)
        total_api_calls = len(token_usage_tracker["calls"])
        flow_type = "full_flow" if total_api_calls >= 3 else "early_exit"
        
        token_usage_summary = {
            "total_tokens_used": token_usage_tracker["total_toon_tokens"],
            "total_tokens_without_toon": token_usage_tracker["total_json_tokens"],
            "total_savings": token_usage_tracker["total_savings"],
            "total_savings_percent": round(token_usage_tracker["total_savings_percent"], 2),
            "total_api_calls": total_api_calls,
            "flow_optimization": flow_type,
            "optimized_from_6_calls": True,  # Indicates we're using the optimized 3-call flow
            "breakdown_by_call": [
                {
                    "call": call["call_name"],
                    "tokens_used": call["toon_tokens"],
                    "tokens_without_toon": call["json_tokens"],
                    "savings": call["savings"],
                    "savings_percent": round(call["savings_percent"], 2)
                }
                for call in token_usage_tracker["calls"]
            ]
        }
        
        # Log comprehensive token usage
        print(f"\n{'='*60}")
        print(f"TOKEN USAGE SUMMARY")
        print(f"{'='*60}")
        print(f"Total Tokens Used (TOON): {token_usage_tracker['total_toon_tokens']}")
        print(f"Total Tokens Without TOON: {token_usage_tracker['total_json_tokens']}")
        print(f"Total Savings: {token_usage_tracker['total_savings']} tokens ({token_usage_tracker['total_savings_percent']:.2f}%)")
        print(f"\nBreakdown by Call:")
        for call in token_usage_tracker["calls"]:
            print(f"  {call['call_name']}:")
            print(f"    TOON Tokens: {call['toon_tokens']}")
            print(f"    JSON Tokens: {call['json_tokens']}")
            print(f"    Savings: {call['savings']} tokens ({call['savings_percent']:.2f}%)")
        print(f"{'='*60}")
        
        # ================================================================
        # COMPREHENSIVE IMPROVEMENT SUMMARY
        # Show all enhancements applied for lengthy question accuracy
        # ================================================================
        print(f"\n{'='*60}")
        print(f"IMPROVEMENTS APPLIED FOR LENGTHY QUESTION ACCURACY")
        print(f"{'='*60}")
        print(f"[OK] Priority 1: Chunk Quality Assessment")
        if 'step3_decision' in locals():
            print(f"  • Data Quality: {step3_decision.get('data_quality', 'N/A')}")
            print(f"  • Relevance Quality: {step3_decision.get('relevance_quality', 'N/A')} (avg_distance={step3_decision.get('avg_chunk_distance', 0):.3f})")
            print(f"  • Confidence Score: {step3_decision.get('confidence_score', 0)}/100")
        
        print(f"\n[OK] Priority 2: Dynamic Model Selection")
        if 'model_score_breakdown' in locals():
            print(f"  • Selected Model: {selected_model}")
            print(f"  • Total Score: {base_model_score if 'base_model_score' in locals() else 'N/A'}")
            print(f"  • Factor Breakdown:")
            for factor, (score, detail) in model_score_breakdown.items():
                print(f"    - {factor}: +{score} ({detail})")
        
        print(f"\n[OK] Priority 3: Answer Mode Adaptation")
        print(f"  • Answer Mode: {answer_mode}")
        print(f"  • Response Length: {response_length}")
        print(f"  • Response Depth: {response_depth}")
        print(f"  • Chunks Retrieved: {len(source_chunks)}")
        
        print(f"\n[OK] Priority 4: Query Refinement")
        if refined_question != request.question:
            print(f"  • Original: {request.question[:80]}...")
            print(f"  • Refined: {refined_question[:80]}...")
        else:
            print(f"  • Query: {request.question[:80]}...")
        
        print(f"\n[OK] Priority 5: Per-Document Parameters Applied")
        print(f"  • Documents Processed: {len(relevant_documents)}")
        print(f"  • Total Chunks: {len(source_chunks)}")
        if len(relevant_documents) > 0:
            print(f"  • Avg Chunks/Doc: {len(source_chunks) / len(relevant_documents):.1f}")
        
        # Calculate processing time
        end_time = time.time()
        processing_time = end_time - start_time
        
        print(f"\n[OK] Priority 6: Confidence Tracking")
        print(f"  • Overall Confidence: {step3_decision.get('confidence_score', 'N/A') if 'step3_decision' in locals() else 'N/A'}%")
        print(f"  • Data Quality: {step3_decision.get('data_quality', 'N/A') if 'step3_decision' in locals() else 'N/A'}")
        print(f"  • Processing Time: {processing_time:.2f}s")
        print(f"{'='*60}\n")
        
        # Log query with comprehensive data (if user context available, otherwise log as system query)
        
        # Log query with comprehensive data (if user context available, otherwise log as system query)
        # Note: ask_question endpoint may be called without authentication
        try:
            # Use system user (ID 1) if no user, or create a default system user
            # First check if system user exists
            system_user = db.query(User).filter(User.id == 1).first()
            if not system_user:
                # Create a system user for logging
                from app.core.security import get_password_hash
                system_user = User(
                    name="System",
                    email="system@askmojo.com",
                    password=get_password_hash("system"),  # Hashed password
                    role="system",
                    is_active=True
                )
                db.add(system_user)
                db.commit()
                db.refresh(system_user)
            
            # Get relevant documents if available (they should be defined earlier in the function)
            docs_to_log = []
            if 'relevant_documents' in locals() and relevant_documents:
                docs_to_log = relevant_documents
            
            # Prepare JSON strings for storage (truncate if too long for SQLite)
            # SQLite TEXT can store up to ~1GB, but we'll limit to 10MB per field for comprehensive logging
            max_json_length = 10000000  # 10MB - increased to store complete API call data
            
            # Store comprehensive JSON data with all information
            # 1. Token Usage JSON - Complete breakdown with all API calls
            token_usage_json_str = None
            if token_usage_summary:
                # Enhance token_usage_summary with additional metadata
                enhanced_token_usage = {
                    **token_usage_summary,
                    "slack_user_query": request.question,  # Original Slack user query
                    "slack_user_email": request.slack_user_email,  # Slack user email
                    "processing_time_seconds": processing_time,
                    "timestamp": datetime.utcnow().isoformat() if 'datetime' in globals() else None
                }
                token_usage_json_str = json.dumps(enhanced_token_usage, indent=2)
                if len(token_usage_json_str) > max_json_length:
                    token_usage_json_str = token_usage_json_str[:max_json_length] + "...[truncated]"
            
            # 2. API Calls JSON - Complete information for all OpenAI API calls
            # Each API call includes: full request prompt, full response, model, tokens, savings, etc.
            api_calls_json_str = None
            if api_call_responses:
                # Enhance API calls with additional context
                enhanced_api_calls = []
                for call in api_call_responses:
                    enhanced_call = {
                        **call,
                        "slack_user_query": request.question,  # Original query for context
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    enhanced_api_calls.append(enhanced_call)
                api_calls_json_str = json.dumps(enhanced_api_calls, indent=2)
                if len(api_calls_json_str) > max_json_length:
                    api_calls_json_str = api_calls_json_str[:max_json_length] + "...[truncated]"
            
            # Determine response type and API call count based on flow
            response_type = token_usage_summary.get("flow_optimization", "full_flow") if token_usage_summary else "full_flow"
            total_api_calls_logged = token_usage_summary.get("total_api_calls", len(token_usage_tracker["calls"])) if token_usage_summary else len(token_usage_tracker["calls"])
            # 3. TOON Savings JSON - Complete TOON savings breakdown
            toon_savings_json_str = None
            if toon_savings_breakdown:
                # Enhance TOON savings with additional metadata
                enhanced_toon_savings = {
                    **toon_savings_breakdown,
                    "slack_user_query": request.question,  # Original query for context
                    "total_api_calls": total_api_calls_logged,
                    "flow_type": response_type,
                    "timestamp": datetime.utcnow().isoformat()
                }
                toon_savings_json_str = json.dumps(enhanced_toon_savings, indent=2)
                if len(toon_savings_json_str) > max_json_length:
                    toon_savings_json_str = toon_savings_json_str[:max_json_length] + "...[truncated]"
            
            # Truncate answer if too long (SQLite TEXT limit is large, but we'll limit to 10MB for performance)
            max_answer_length = 10000000  # 10MB
            stored_answer = answer
            if stored_answer and len(stored_answer) > max_answer_length:
                stored_answer = stored_answer[:max_answer_length] + "...[truncated]"
            
            query_log = QueryLog(
                user_id=system_user.id,  # Use system user for public queries
                query=request.question,
                intent=None,  # Can be extracted from AI analysis if needed
                response_type=response_type,  # "full_flow", "early_exit_step1", "early_exit_step2"
                used_internal_only=False,  # Can be determined from document filtering
                answer=stored_answer,  # Store the original answer (not Slack formatted)
                processing_time_seconds=processing_time,
                total_tokens_used=token_usage_summary.get("total_tokens_used") if token_usage_summary else None,
                total_tokens_without_toon=token_usage_summary.get("total_tokens_without_toon") if token_usage_summary else None,
                token_savings=token_usage_summary.get("total_savings") if token_usage_summary else None,
                token_savings_percent=token_usage_summary.get("total_savings_percent") if token_usage_summary else None,
                token_usage_json=token_usage_json_str,
                api_calls_json=api_calls_json_str,
                toon_savings_json=toon_savings_json_str,
                slack_user_email=request.slack_user_email  # Store Slack user email if provided
            )
            db.add(query_log)
            db.commit()
            db.refresh(query_log)
            
            # Log sources if available
            if docs_to_log:
                from app.sqlite.models import QuerySource
                for doc in docs_to_log:
                    if isinstance(doc, dict) and "document_id" in doc:
                        source = QuerySource(
                            query_id=query_log.id,
                            document_id=doc["document_id"],
                            chunk_id=None,
                            relevance_score=doc.get("score", 0.0)
                        )
                        db.add(source)
                db.commit()
            
            print(f"[OK] Query logged successfully (ID: {query_log.id}, Time: {processing_time:.2f}s, API Calls: {total_api_calls_logged}, Flow: {response_type}, Slack User: {request.slack_user_email or 'N/A'})")
        except Exception as e:
            # Don't fail the request if logging fails
            print(f"Error logging query: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # Return answer with token usage, savings data, and API call responses
        # Build sources list (top 3 document titles)
        sources_list = []
        try:
            if 'relevant_documents' in locals() and relevant_documents:
                for d in relevant_documents[:3]:
                    title = _humanize_title(d.get("title") or d.get("document_title") or None)
                    if title and title not in sources_list:
                        sources_list.append(title)
        except Exception:
            sources_list = None

        # Build follow-up suggestions
        try:
            followups = generate_followups(chunks_json, intent_hints if 'intent_hints' in locals() else {}, intent_hints.get("sales_intent") if 'intent_hints' in locals() else None, step3_decision.get("confidence_score", 50) if 'step3_decision' in locals() else 50, proof_snippet if 'proof_snippet' in locals() else None)
        except Exception:
            followups = None

        return AskResponse(
            answer=slack_formatted_answer,
            token_usage=token_usage_summary,
            toon_savings=toon_savings_breakdown,
            api_calls=api_call_responses,
            followups=followups,
            sources=sources_list
        )
        
    except Exception as e:
        print(f"Error in ask endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing question: {str(e)}"
        )
