from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.sqlite.models import Document, Category


def _normalize_label(value: Optional[str]) -> str:
    """
    Normalize a category / collection / legacy category string
    into a lowercase, space-separated label for matching.
    """
    if not value:
        return ""
    return value.strip().lower().replace("_", " ").replace("-", " ")


def infer_doc_type_from_category_name(name_or_collection: Optional[str]) -> str:
    """
    Map a Category.name or Category.collection_name to a normalized
    document type used by the retrieval pipeline.

    Normalized types:
      - "proposal"
      - "case_study"
      - "solution"   (also used for 'services')
            - "policy"
      - "other"
    """
    label = _normalize_label(name_or_collection)
    if not label:
        return "other"

    # Proposals (e.g. "Proposals", "Proposal Library")
    if "proposal" in label:
        return "proposal"

    # Case studies (e.g. "Case Studies", "Customer Case Study")
    if "case" in label and "study" in label:
        return "case_study"

    # Solutions / Services (e.g. "Solutions", "Services", "Solution Accelerators")
    if "solution" in label or "service" in label:
        return "solution"

    # Policies (e.g. "Security Policies", "HR Policy")
    if "policy" in label or "policies" in label:
        return "policy"

    return "other"


def infer_doc_type_for_document(document: Document, db: Optional[Session] = None) -> str:
    """
    Infer the logical document type (proposal / case_study / solution / policy / other)
    for a given Document using:
      1. Its Category (name / collection_name) when available
      2. Legacy document.category as a fallback
    """
    # 1) Check Document Title / Filename first (more specific)
    # This allows correctly identifying a "Case Study" inside a "Banking" category
    label = _normalize_label(document.title + " " + (document.file_name or ""))
    
    # Proposals
    if "proposal" in label:
        return "proposal"
    
    # Case studies
    if ("case" in label and "study" in label) or "success story" in label:
        return "case_study"
    
    # Solutions / Services
    if "solution" in label or "service" in label:
        return "solution"

    # Policies
    if "policy" in label or "policies" in label:
        return "policy"

    # 2) Fallback to Category.name / collection_name
    if document.category_ref is not None:
        # Relationship already loaded
        cat = document.category_ref
        doc_type = infer_doc_type_from_category_name(cat.name or cat.collection_name)
        if doc_type != "other":
            return doc_type

    if document.category_id and db is not None:
        cat: Optional[Category] = (
            db.query(Category).filter(Category.id == document.category_id).first()
        )
        if cat is not None:
            doc_type = infer_doc_type_from_category_name(cat.name or cat.collection_name)
            if doc_type != "other":
                return doc_type

    # 3) Fallback to legacy string category on Document
    if document.category:
        doc_type = infer_doc_type_from_category_name(document.category)
        if doc_type != "other":
            return doc_type

    return "other"

