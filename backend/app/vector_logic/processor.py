import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.ocr_pipeline import run_ocr_pipeline, convert_ocr_chunks_for_chromadb
from app.sqlite.database import SessionLocal
from app.sqlite.models import Document, DocumentVersion, DocumentChunk, DocumentUploadLog
from app.utils.concurrency import ConcurrencyManager
from app.vector_logic.doc_types import infer_doc_type_for_document
from app.vector_logic.vector_store import (
    sanitize_chunks_for_storage,
    store_chunks_in_chromadb,
    store_document_in_master_collection,
)
from app.vector_logic.description_generator import generate_description_from_ocr
from monitoring.error_handler import handle_error

logger = logging.getLogger(__name__)

async def process_document_async(
    document_id: int,
    delay_seconds: int = 5,
    collection_name: str = "pdf_pages",
    persist_directory: str = None
):
    """
    Background task to process a document after a delay.
    
    Args:
        document_id: ID of the document to process
        delay_seconds: Delay before processing starts
        collection_name: ChromaDB collection name
        persist_directory: Path to ChromaDB storage (defaults to ../vector_db/chroma_db)
    """
    # Wait for the specified delay
    await asyncio.sleep(delay_seconds)
    
    # Create a new database session for this background task
    db = SessionLocal()
    
    try:
        # Get the document
        document = db.query(Document).filter(Document.id == document_id).first()
        
        if not document:
            logger.error(f"Document {document_id} not found")
            return
        
        if not document.file_path:
            logger.error(f"Document {document_id} has no file_path")
            return
        
        if document.processed:
            logger.info(f"Document {document_id} already processed")
            return

        # Fallback document type used when OCR metadata doesn't include one.
        doc_type = infer_doc_type_for_document(document, db)
        
        # ═══════════════════════════════════════════════════════════════════════════
        # Concurrency Control: Check admin capacity (max 15 parallel per admin)
        # ═══════════════════════════════════════════════════════════════════════════
        admin_id = document.uploaded_by
        current_count = ConcurrencyManager.get_concurrent_count(admin_id)
        remaining_capacity = ConcurrencyManager.get_remaining_capacity(admin_id)
        
        logger.info(
            f"Processing document {document_id} for admin {admin_id}. "
            f"Concurrent: {current_count}/15, Remaining capacity: {remaining_capacity}"
        )
        
        if remaining_capacity == 0:
            logger.warning(
                f"Admin {admin_id} has reached max concurrent processing limit (15 documents). "
                f"Document {document_id} will be queued."
            )
            # Don't return - we'll let FastAPI BackgroundTasks retry later
            # Or we could raise an exception to requeue
            db.close()
            return
        
        # Try to acquire a concurrency slot for this admin
        slot_acquired = await ConcurrencyManager.acquire_slot(admin_id)
        if not slot_acquired:
            logger.warning(
                f"Could not acquire processing slot for admin {admin_id} "
                f"(document {document_id}). Will retry later."
            )
            db.close()
            return
        
        try:
            # Update upload log - mark processing as started
            upload_log = db.query(DocumentUploadLog).filter(
                DocumentUploadLog.document_id == document_id
            ).first()
            if upload_log:
                upload_log.processing_started = True
                upload_log.processing_stage = "QUEUED"
                db.commit()
            
            logger.info(f"Starting vector processing for document {document_id}: {document.title}")

            # Prefer the collection name passed by the upload route (Category.collection_name).
            # Fallback to deriving from the stored category label for legacy records only.
            if not collection_name:
                if not document.category:
                    collection_name = "documents"
                else:
                    collection_name = document.category.lower().replace(" ", "_")

            # ═══════════════════════════════════════════════════════════════════════════
            # Check if file exists (Robust to moved project folders)
            # ═══════════════════════════════════════════════════════════════════════════
            from app.vector_logic.routes import UPLOAD_DIR
            stored_path = Path(document.file_path)
            file_path = stored_path
            
            if not file_path.exists():
                # Try fallback: look for the filename in the current UPLOAD_DIR
                fallback_path = UPLOAD_DIR / stored_path.name
                if fallback_path.exists():
                    logger.info(f"Using fallback path: {fallback_path}")
                    file_path = fallback_path
                else:
                    logger.error(f"File not found at {stored_path} or fallback {fallback_path}")
                    return

            extraction_start_time = time.time()

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 1: Run OCR Pipeline (PDF → PaddleOCR → OpenAI structuring)
            # ═══════════════════════════════════════════════════════════════════════════
            chunks = []
            global_meta = {}
            extraction_method = "ocr_pipeline"
            extraction_error = None
            ocr_pages_text = None
            description_task = None
            description_start = None

            try:
                if upload_log:
                    upload_log.processing_stage = "OCR_PIPELINE"
                    db.commit()
                logger.info(f"[OCR] Starting OCR pipeline for document {document_id}: {document.title}")
                ocr_output = await asyncio.to_thread(run_ocr_pipeline, str(file_path))
                global_meta = ocr_output.get("metadata", {})

                try:
                    ocr_pages_text = [(p.get("raw_text") or "") for p in (ocr_output.get("pages") or []) if isinstance(p, dict)]
                except Exception:
                    ocr_pages_text = None

                if ocr_output and ocr_output.get("chunks"):
                    logger.info(f"[OCR] Pipeline produced {len(ocr_output['chunks'])} raw chunks for document {document_id}")
                    # NEW: Use inline description if available from OCR pipeline
                    inline_description = ocr_output.get("document_description")
                    
                    # Pass the structured description to the adapter so it flows to ChromaDB
                    chunks = convert_ocr_chunks_for_chromadb(
                        ocr_output, 
                        document_id=document.id,
                        document_description=inline_description
                    )

                    if inline_description:
                        logger.info(f"[OCR] Using inline document_description for document {document_id}")
                        new_description = json.dumps(inline_description, indent=2, ensure_ascii=False)
                        description_task = None # Signal that we already have it
                    else:
                        logger.info(f"[OCR] No inline description found, starting separate task for document {document_id}")
                        description_task = asyncio.create_task(
                            asyncio.to_thread(
                                generate_description_from_ocr,
                                title=document.title,
                                category=document.category,
                                ocr_metadata=global_meta,
                                ocr_pages_text=ocr_pages_text,
                                chunks=chunks,
                                openai_api_key=settings.openai_api_key,
                            )
                        )
                else:
                    logger.warning(f"[OCR] Pipeline returned no chunks for document {document_id}")
                    extraction_error = "OCR pipeline returned empty result"

            except Exception as e:
                logger.error(f"[OCR] Pipeline error for document {document_id}: {str(e)}", exc_info=True)
                try:
                    handle_error(
                        stage="OCR_EXTRACTION_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="ERROR",
                    )
                except Exception:
                    pass
                extraction_error = str(e)

            if not chunks:
                logger.error(f"No chunks extracted from document {document_id}")
                extraction_error = extraction_error or "No chunks extracted from OCR"
                if upload_log:
                    upload_log.processing_error = (f"OCR extraction failed: {extraction_error}")[:500]
                    upload_log.processing_started = True
                    upload_log.processing_completed = False
                    upload_log.processing_stage = "FAILED" # Added this line
                    db.commit()
                return

            extraction_time = time.time() - extraction_start_time
            logger.info(f"Extraction completed in {extraction_time:.2f}s with {len(chunks)} chunks")

            # Log extraction time
            logger.info(f"Extraction method: {extraction_method}, time: {extraction_time:.2f}s")

            # Attach document metadata for vector store
            for chunk in chunks:
                chunk["version"] = 1

            chunks = sanitize_chunks_for_storage(chunks)
            if not chunks:
                logger.error(f"No valid non-empty chunks remain after sanitization for document {document_id}")
                extraction_error = "No valid non-empty chunks remain after sanitization"
                if upload_log:
                    upload_log.processing_error = (f"OCR yielded empty/invalid chunks after sanitization: {extraction_error}")[:500]
                    upload_log.processing_started = True
                    upload_log.processing_completed = False
                    upload_log.processing_stage = "FAILED" # Added this line
                    db.commit()
                return

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 6.5: Generate document description from OCR-derived content
            # ═══════════════════════════════════════════════════════════════════════════
            tokens_info = None
            description_elapsed = 0.0
            try:
                if upload_log: # Added this block
                    upload_log.processing_stage = "DESCRIPTION_GENERATION"
                    db.commit()
                if description_task is not None:
                    new_description, tokens_info = await description_task
                    document.description = new_description
                elif inline_description:
                    # new_description already set above
                    document.description = new_description
                else:
                    # Final fallback if both failed somehow
                    description_start = time.time()
                    new_description, tokens_info = await asyncio.to_thread(
                        generate_description_from_ocr,
                        title=document.title,
                        category=document.category,
                        ocr_metadata=global_meta,
                        ocr_pages_text=ocr_pages_text,
                        chunks=chunks,
                        openai_api_key=settings.openai_api_key,
                    )
                    document.description = new_description

                db.commit()
                if description_start is not None:
                    description_elapsed = time.time() - description_start
            except Exception as e:
                logger.error(f"Description generation failed for document {document_id}: {str(e)}", exc_info=True)
                if upload_log:
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = (f"Description generation failed: {str(e)}")[:500]
                    upload_log.processing_started = True
                    upload_log.processing_completed = False
                    db.commit()
                return

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 6: Store in ChromaDB
            # ═══════════════════════════════════════════════════════════════════════════
            if upload_log:
                upload_log.processing_stage = "VECTOR_STORAGE"
                db.commit()
            if persist_directory is None:
                base_dir = Path(__file__).resolve().parents[1]  # Go up to app/ directory
                persist_directory = str(base_dir / "vector_db" / "chroma_db")

            logger.info(f"Storing {len(chunks)} chunks in ChromaDB collection: {collection_name}")
            logger.info(f"ChromaDB path: {persist_directory}")
            try:
                collection = store_chunks_in_chromadb(
                    chunks=chunks,
                    collection_name=collection_name,
                    persist_directory=persist_directory,
                )
                logger.info(f"✓ Successfully stored {len(chunks)} chunks in ChromaDB collection '{collection_name}'")
                
                # ── FIX 5: Build/Update BM25 Sparse Index ────────────────────
                from app.vector_logic.vector_store import build_bm25_index
                chunk_texts = [c["text"] for c in chunks]
                chunk_ids = [f"doc_{document.id}_chunk_{i+1}" for i in range(len(chunks))]
                build_bm25_index(collection_name, chunk_texts, chunk_ids)
                logger.info(f"✓ Successfully updated BM25 index for '{collection_name}'")
                
            except Exception as e:
                logger.error(f"ChromaDB storage error for document {document_id}: {str(e)}")
                # Monitor: VECTOR store stage
                try:
                    handle_error(
                        stage="VECTOR_STORE_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="CRITICAL",
                    )
                except Exception:
                    pass
                extraction_error = f"ChromaDB storage failed: {str(e)}"
                if upload_log:
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = extraction_error[:500]
                    db.commit()
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 7: Create DocumentVersion record
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                if upload_log: # Added this block
                    upload_log.processing_stage = "CREATE_VERSION"
                    db.commit()
                document_version = DocumentVersion(
                    document_id=document.id,
                    version=1,  # First version
                    file_path=str(file_path),
                    checksum=None  # Can add checksum calculation later
                )
                db.add(document_version)
                db.commit()
                db.refresh(document_version)
                logger.info(f"Created DocumentVersion record for document {document_id}")
            except Exception as e:
                logger.error(f"Error creating DocumentVersion: {str(e)}")
                if upload_log: # Added this block
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = f"Error creating DocumentVersion: {str(e)}"[:500]
                    db.commit()
                try:
                    handle_error(
                        stage="VECTOR_STORE_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="ERROR",
                    )
                except Exception:
                    pass
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 8: Create DocumentChunk records
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                if upload_log: # Added this block
                    upload_log.processing_stage = "CREATE_CHUNKS"
                    db.commit()
                for chunk in chunks:
                    document_chunk = DocumentChunk(
                        document_id=document.id,
                        version_id=document_version.id,
                        version=document_version.version,
                        chunk_index=chunk.get("chunk_index", 0),
                        page_number=chunk.get("page_number"),
                        section=chunk.get("section")
                    )
                    db.add(document_chunk)
                
                db.commit()
                logger.info(f"Created {len(chunks)} DocumentChunk records")
            except Exception as e:
                logger.error(f"Error creating DocumentChunk records: {str(e)}")
                if upload_log: # Added this block
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = f"Error creating DocumentChunk records: {str(e)}"[:500]
                    db.commit()
                try:
                    handle_error(
                        stage="VECTOR_STORE_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="ERROR",
                    )
                except Exception:
                    pass
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 9: Store document in master_docs collection
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                if upload_log:
                    upload_log.processing_stage = "MASTER_COLLECTION"
                    db.commit()
                logger.info(f"Storing document metadata in master_docs collection...")
                store_document_in_master_collection(
                    document_id=document.id,
                    title=document.title,
                    description=document.description or "",
                    category=document.category,
                    source_type=document.source_type,
                    persist_directory=persist_directory,
                    doc_type=global_meta.get("doc_type", doc_type),
                    domain=global_meta.get("domain"),
                    solution_area=global_meta.get("solution_area"),
                    industry=global_meta.get("industry"),
                    technology=global_meta.get("technology"),
                    use_case=global_meta.get("use_case"),
                    entity=global_meta.get("entity"),
                    created_at=global_meta.get("created_at")
                )
                logger.info(f"Successfully stored document metadata in master_docs")
            except Exception as e:
                logger.error(f"Error storing in master_docs: {str(e)}")
                # Don't fail the entire process if master collection fails
                if upload_log: # Added this block
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = f"Error storing in master_docs: {str(e)}"[:500]
                    db.commit()
                try:
                    handle_error(
                        stage="VECTOR_STORE_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="ERROR",
                    )
                except Exception:
                    pass
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 10: Mark document as processed
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                document.processed = True
                db.commit()
                
                # Update upload log
                if upload_log:
                    upload_log.processing_completed = True
                    upload_log.processing_stage = "COMPLETED"
                    upload_log.processed_at = datetime.utcnow()
                    upload_log.chunk_count = len(chunks)
                    upload_log.document_description = document.description
                    upload_log.description_length = len(document.description) if document.description else 0
                    upload_log.description_generated = True
                    upload_log.description_generation_time_seconds = description_elapsed
                    if tokens_info:
                        upload_log.description_tokens_used = tokens_info.get("total_tokens")
                        upload_log.description_tokens_prompt = tokens_info.get("prompt_tokens")
                        upload_log.description_tokens_completion = tokens_info.get("completion_tokens")
                    db.commit()
                
                logger.info(
                    f"✓ Successfully processed document {document_id} "
                    f"({len(chunks)} chunks, {extraction_method}) for admin {admin_id}"
                )
            except Exception as e:
                logger.error(f"Error marking document as processed: {str(e)}")
                if upload_log: # Added this block
                    upload_log.processing_stage = "FAILED"
                    upload_log.processing_error = f"Error marking document as processed: {str(e)}"[:500]
                    db.commit()
                try:
                    handle_error(
                        stage="VECTOR_STORE_STAGE",
                        user=str(admin_id),
                        query=document.title or str(file_path),
                        error=e,
                        severity="ERROR",
                    )
                except Exception:
                    pass
        
        except Exception as e:
            logger.error(f"Error processing document {document_id}: {str(e)}", exc_info=True)
            try:
                handle_error(
                    stage="ADOBE_EXTRACTION_STAGE",
                    user=str(admin_id),
                    query=document.title or str(file_path),
                    error=e,
                    severity="CRITICAL",
                )
            except Exception:
                pass
            # Update upload log with error
            try:
                upload_log = db.query(DocumentUploadLog).filter(
                    DocumentUploadLog.document_id == document_id
                ).first()
                if upload_log:
                    upload_log.processing_error = str(e)[:500]  # Limit error message length
                    upload_log.processing_started = True
                    upload_log.processing_stage = "FAILED"
                    db.commit()
            except Exception:
                pass
        
        finally:
            # Always release the concurrency slot
            ConcurrencyManager.release_slot(admin_id)
            stats = ConcurrencyManager.get_stats(admin_id, db)
            logger.info(
                f"Released concurrency slot for admin {admin_id}. "
                f"Active: {stats['concurrent_processing']}/15, Queue: {stats['queue_length']}"
            )
        
    except Exception as e:
        logger.error(f"Critical error processing document {document_id}: {str(e)}", exc_info=True)
        try:
            handle_error(
                stage="ADOBE_EXTRACTION_STAGE",
                user=str(document_id),
                query=str(document_id),
                error=e,
                severity="FATAL",
            )
        except Exception:
            pass
    
    finally:
        db.close()


def process_document_background(
    document_id: int,
    delay_seconds: int = 5,
    collection_name: str = "pdf_pages",
    persist_directory: str = None
):
    """
    Synchronous wrapper to run the async processing task.
    This can be used with FastAPI BackgroundTasks.
    """
    # Create a new event loop for this background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(
            process_document_async(
                document_id=document_id,
                delay_seconds=delay_seconds,
                collection_name=collection_name,
                persist_directory=persist_directory
            )
        )
    finally:
        loop.close()
