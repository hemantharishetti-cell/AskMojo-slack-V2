"""Backfill `documents.domain_id` and `documents.doc_type`.

This project now treats `documents.domain_id` as authoritative and required.
For existing rows that still have NULL domain_id, this script assigns:
  - the single domain mapped to the document's category (if exactly one exists)
  - otherwise the "Unassigned" domain

It also persists a deterministic `documents.doc_type` for SQL-only filters.

Usage:
  python backfill_document_domains.py

Safe to re-run.
"""

from __future__ import annotations

from app.sqlite.database import get_db_context
from app.sqlite.models import CategoryDomain, Document, Domain, DocumentUploadLog
from app.vector_logic.doc_types import infer_doc_type_for_document


UNASSIGNED_DOMAIN_NAME = "Unassigned"


def _get_or_create_unassigned_domain_id(db) -> int:
    dom = db.query(Domain).filter(Domain.name == UNASSIGNED_DOMAIN_NAME).first()
    if dom is not None:
        return dom.id

    dom = Domain(
        name=UNASSIGNED_DOMAIN_NAME,
        description="Fallback domain for legacy documents that were uploaded without an explicit domain.",
        is_active=True,
    )
    db.add(dom)
    db.flush()
    return dom.id


def main() -> None:
    with get_db_context() as db:
        unassigned_id = _get_or_create_unassigned_domain_id(db)

        docs = db.query(Document).filter(Document.domain_id.is_(None)).all()
        updated = 0
        ambiguous = 0

        for doc in docs:
            chosen_domain_id = None
            if doc.category_id is not None:
                domain_ids = (
                    db.query(CategoryDomain.domain_id)
                    .filter(CategoryDomain.category_id == doc.category_id)
                    .distinct()
                    .all()
                )
                unique_ids = sorted({row[0] for row in domain_ids if row and row[0] is not None})
                if len(unique_ids) == 1:
                    chosen_domain_id = unique_ids[0]
                elif len(unique_ids) > 1:
                    ambiguous += 1

            doc.domain_id = chosen_domain_id or unassigned_id

            # Also persist doc_type (deterministic; doesn't use LLM)
            try:
                doc.doc_type = infer_doc_type_for_document(doc, db)
            except Exception:
                doc.doc_type = doc.doc_type or "other"

            updated += 1

        # Keep upload logs consistent (best-effort)
        logs = db.query(DocumentUploadLog).filter(DocumentUploadLog.domain_id.is_(None)).all()
        for log in logs:
            log.domain_id = unassigned_id

        print(
            f"Backfill complete: updated {updated} documents; "
            f"{ambiguous} had multiple category->domain mappings and were set to '{UNASSIGNED_DOMAIN_NAME}'. "
            f"Updated {len(logs)} upload logs."
        )


if __name__ == "__main__":
    main()
