import asyncio
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.ocr_pipeline.adapter import convert_ocr_chunks_for_chromadb
from app.ocr_pipeline.pipeline import run_ocr_pipeline
from app.ocr_pipeline.metadata import MetadataAugmentation
from app.sqlite.database import SessionLocal
from app.sqlite.models import Document
from app.vector_logic.doc_types import infer_doc_type_for_document
from app.vector_logic.vector_store import store_chunks_in_chromadb, store_document_in_master_collection

logger = logging.getLogger(__name__)


async def ingest_document(pdf_path: str, category: str = "general"):
    logger.info("🚀 Starting ingestion for: %s", pdf_path)
    file_path = Path(pdf_path)
    if not file_path.exists():
        logger.error("File not found: %s", pdf_path)
        return

    db: Session = SessionLocal()
    try:
        doc_title = file_path.stem.replace("-", " ").replace("_", " ").title()
        document = db.query(Document).filter(Document.title == doc_title).first()
        if not document:
            document = Document(
                title=doc_title,
                file_path=str(file_path),
                file_name=file_path.name,
                category=category,
                processed=False,
                uploaded_by=1,
            )
            db.add(document)
            db.commit()
            db.refresh(document)
            logger.info("Created new Document record ID: %s", document.id)
        else:
            logger.info("Using existing Document record ID: %s", document.id)

        logger.info("[OCR] Running PaddleOCR + GPT structuring...")
        ocr_output = await asyncio.to_thread(run_ocr_pipeline, str(file_path))

        if not ocr_output or not ocr_output.get("chunks"):
            logger.error("OCR failed to produce chunks.")
            return

        chunks = convert_ocr_chunks_for_chromadb(ocr_output, document_id=document.id)
        logger.info("[OCR] Extracted %d chunks", len(chunks))

        doc_type = infer_doc_type_for_document(document, db)
        chunks = MetadataAugmentation.augment_chunks(
            chunks=chunks,
            document_id=document.id,
            document_title=document.title,
            category=category,
            doc_type=doc_type,
        )
        for chunk in chunks:
            chunk["version"] = 1

        collection_name = category.lower().replace(" ", "_")
        logger.info("[Vector] Storing chunks in collection: %s", collection_name)
        store_chunks_in_chromadb(chunks=chunks, collection_name=collection_name)
        
        # New: Update structured collection descriptor
        try:
            from app.vector_logic.vector_store import update_collection_descriptor
            update_collection_descriptor(collection_name, {
                "doc_type": doc_type,
                "category": category,
                "domain": chunks[0].get("domain") if chunks else None,
                "industry": chunks[0].get("industry") if chunks else None,
                "technology": chunks[0].get("technology") if chunks else None,
                "solution_area": chunks[0].get("solution_area") if chunks else None,
                "entity": chunks[0].get("entity") if chunks else None,
                "use_case": chunks[0].get("use_case") if chunks else None,
            })
        except Exception as e:
            logger.warning("[METADATA] Error updating collection descriptor: %s", e)

        logger.info("[Vector] Updating master_docs collection...")
        store_document_in_master_collection(
            document_id=document.id,
            title=document.title,
            description=document.description or "",
            category=category,
            doc_type=doc_type,
        )

        document.processed = True
        db.commit()
        logger.info("✅ Ingestion complete for document: %s", doc_title)

    except Exception as e:
        logger.error("Ingestion failed: %s", e, exc_info=True)
    finally:
        db.close()
