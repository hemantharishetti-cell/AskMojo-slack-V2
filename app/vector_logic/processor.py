import asyncio
import logging
import time
from pathlib import Path
from datetime import datetime

from app.sqlite.models import Document, DocumentVersion, DocumentChunk, DocumentUploadLog, Category, ExtractedContent
from app.vector_logic.chunking import chunk_by_pages
from app.vector_logic.vector_store import (
    store_chunks_in_chromadb,
    store_document_in_master_collection
)
from app.vector_logic.doc_types import infer_doc_type_for_document
from app.core.config import settings
from app.sqlite.database import SessionLocal

# Adobe PDF Services Integration
from app.pdf_extraction.adobe_client import get_adobe_extractor
from app.pdf_extraction.extraction_cache import ExtractionCacheManager
from app.pdf_extraction.structured_chunking import StructuredChunker
from app.pdf_extraction.structured_chunker_v2 import StructuredChunkerV2
from app.debug_extraction_analyzer import save_extraction_for_analysis
from app.pdf_extraction.metadata_augmentation import MetadataAugmentation
from app.pdf_extraction.rate_limiter import RateLimiter
from app.pdf_extraction.concurrency_manager import ConcurrencyManager, ConcurrencyContextManager
from app.pdf_extraction.normalizer import normalize_adobe_elements, save_normalized_result

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
                db.commit()
            
            logger.info(f"Starting vector processing for document {document_id}: {document.title}")

            # Use category as collection name if not provided, or derive from document
            if not document.category:
                collection_name = "documents"
            else:
                collection_name = document.category.lower().replace(" ", "_")

            # Check if file exists
            file_path = Path(document.file_path)
            if not file_path.exists():
                logger.error(f"File not found: {file_path}")
                return

            extraction_start_time = time.time()

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 1: Check rate limit before attempting extraction
            # ═══════════════════════════════════════════════════════════════════════════
            rate_check = RateLimiter.can_extract(db)
            if not rate_check.get("allowed"):
                logger.warning(f"Rate limit check failed: {rate_check.get('reason')}")
                if upload_log:
                    upload_log.processing_error = f"Adobe rate limit: {rate_check.get('reason')}"
                    upload_log.processing_started = True
                    db.commit()
                return

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 2: Try Adobe PDF Extract API (with cache check)
            # ═══════════════════════════════════════════════════════════════════════════
            extracted_json = None
            extraction_method = "adobe_api"
            extraction_error = None

            try:
                logger.info(f"Attempting Adobe PDF extraction for document {document_id}: {document.title}")
                
                # Check cache first
                cached_result = ExtractionCacheManager.get_cached_extraction(str(file_path), db)
                if cached_result:
                    logger.info(f"[ADOBE] Using cached extraction for document {document_id}")
                    extracted_json = cached_result
                    extraction_method = "adobe_cached"
                else:
                    # Not cached, extract from Adobe
                    logger.info(f"[ADOBE] Starting fresh extraction (not in cache) for document {document_id}")
                    adobe_extractor = get_adobe_extractor()
                    logger.info("[ADOBE] AdobeExtractor initialized with OAuth2 token management")
                    # Use async method directly since we're in an async context
                    extracted_json = await adobe_extractor.extract_pdf_async(str(file_path))
                    
                    if extracted_json:
                        logger.info(f"[ADOBE] Extraction successful for document {document_id}")
                        # Cache the result
                        ExtractionCacheManager.store_extraction_result(
                            document_id=document_id,
                            file_path=str(file_path),
                            extraction_json=extracted_json,
                            extraction_method="adobe_api",
                            db=db
                        )
                    else:
                        logger.warning(f"[ADOBE] Extraction returned None/empty for document {document_id} - will fallback to pdfplumber")
                        extraction_error = "Adobe API returned empty result"
            
            except Exception as e:
                logger.error(f"[ADOBE] Extraction error for document {document_id}: {str(e)}", exc_info=True)
                extraction_error = str(e)
                extracted_json = None

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 3: Fallback to pdfplumber if Adobe fails or not configured
            # ═══════════════════════════════════════════════════════════════════════════
            chunks = []
            if not extracted_json:
                if settings.adobe_fallback_to_pdfplumber:
                    logger.warning(f"[FALLBACK] Adobe extraction did not succeed. Falling back to pdfplumber for document {document_id}")
                    extraction_method = "pdfplumber_fallback"
                    
                    try:
                        chunks = chunk_by_pages(str(file_path))
                        if chunks:
                            logger.info(f"[FALLBACK] pdfplumber extracted {len(chunks)} chunks for document {document_id}")
                    except Exception as e:
                        logger.error(f"[FALLBACK] pdfplumber extraction error for document {document_id}: {str(e)}")
                        extraction_error = f"Both Adobe and pdfplumber failed: {str(e)}"
                        return
                else:
                    logger.error(f"[ERROR] Adobe extraction failed and fallback disabled for document {document_id}")
                    extraction_error = "Adobe extraction failed and no fallback configured"
                    return
            else:
                # ═══════════════════════════════════════════════════════════════════════════
                # Step 4: Use structured chunking from Adobe JSON
                # ═══════════════════════════════════════════════════════════════════════════
                # Phase-1: Normalize Adobe output into page-wise schema and persist
                try:
                    normalized = normalize_adobe_elements(extracted_json, document_id=document.id)
                    out_path = save_normalized_result(normalized, document_id=document.id)
                    pages_count = len(normalized.get("pages", []))
                    # Log compact per-type tally for first page (if present)
                    if pages_count > 0:
                        p0 = normalized["pages"][0]
                        logger.info(
                            f"[NORMALIZED] doc={document_id} pages={pages_count} | "
                            f"p1 counts: h1={len(p0['h1'])}, h2={len(p0['h2'])}, h3={len(p0['h3'])}, "
                            f"p={len(p0['p'])}, list={len(p0['list'])}, table={len(p0['table'])}"
                        )
                    logger.info(f"[NORMALIZED] Saved normalized JSON to: {out_path}")
                except Exception as e:
                    logger.warning(f"[NORMALIZED] Could not create/save normalized output: {e}")

                logger.info(f"[ADOBE] Using Adobe extracted JSON for structured chunking")
                logger.info(f"Creating structured chunks from Adobe JSON for document {document_id}")
                try:
                    # Use normalized JSON as input to the deterministic chunker v2
                    chunks = StructuredChunkerV2.chunk_normalized(normalized)
                    logger.info(f"Created {len(chunks)} structured chunks from Adobe JSON")
                except Exception as e:
                    logger.error(f"Structured chunking error for document {document_id}: {str(e)}")
                    extraction_error = f"Structured chunking failed: {str(e)}"
                    return

            if not chunks:
                logger.error(f"No chunks extracted from document {document_id}")
                extraction_error = "No chunks extracted after processing"
                return

            extraction_time = time.time() - extraction_start_time
            logger.info(f"Extraction completed in {extraction_time:.2f}s with {len(chunks)} chunks")

            # Record extraction in database
            try:
                RateLimiter.record_extraction(
                    document_id=document_id,
                    extraction_method=extraction_method,
                    error_message=extraction_error,
                    extraction_time_seconds=extraction_time,
                    db=db
                )
            except Exception as e:
                logger.error(f"Error recording extraction: {str(e)}")

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 5: Augment chunks with metadata
            # ═══════════════════════════════════════════════════════════════════════════
            logger.info(f"Augmenting metadata for {len(chunks)} chunks")
            doc_type = infer_doc_type_for_document(document, db)
            
            # Log chunk details before augmentation
            logger.info("[PROCESSOR] Chunks before augmentation:")
            for chunk in chunks[:2]:  # Show first 2 chunks
                text_preview = chunk.get("text", "")[:100].replace("\n", " ")
                logger.info(f"  - Chunk #{chunk.get('chunk_index')}: {text_preview}...")
            
            # Save extraction and chunks to JSON for analysis
            try:
                save_extraction_for_analysis(
                    document_id=document.id,
                    document_title=document.title,
                    extracted_json=extracted_json,
                    chunks=chunks
                )
            except Exception as e:
                logger.warning(f"Could not save extraction analysis: {str(e)}")
            
            chunks = MetadataAugmentation.augment_chunks(
                chunks=chunks,
                document_id=document.id,
                document_title=document.title,
                category=document.category or "",
                doc_type=doc_type,
                domain=None  # Can be enhanced later
            )
            
            # Log augmented chunks
            logger.info("[PROCESSOR] Chunks after augmentation:")
            for chunk in chunks[:2]:  # Show first 2 chunks
                text_preview = chunk.get("text", "")[:100].replace("\n", " ")
                section = chunk.get("section", "N/A")
                logger.info(f"  - Section '{section}': {text_preview}...")

            # Attach document metadata for vector store
            for chunk in chunks:
                chunk["version"] = 1

            # ═══════════════════════════════════════════════════════════════════════════
            # Step 6: Store in ChromaDB
            # ═══════════════════════════════════════════════════════════════════════════
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
            except Exception as e:
                logger.error(f"ChromaDB storage error for document {document_id}: {str(e)}")
                extraction_error = f"ChromaDB storage failed: {str(e)}"
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 7: Create DocumentVersion record
            # ═══════════════════════════════════════════════════════════════════════════
            try:
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
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 8: Create DocumentChunk records
            # ═══════════════════════════════════════════════════════════════════════════
            try:
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
                return
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 9: Store document in master_docs collection
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                logger.info(f"Storing document metadata in master_docs collection...")
                store_document_in_master_collection(
                    document_id=document.id,
                    title=document.title,
                    description=document.description or "",
                    category=document.category,
                    source_type=document.source_type,
                    persist_directory=persist_directory,
                    doc_type=doc_type,
                )
                logger.info(f"Successfully stored document metadata in master_docs")
            except Exception as e:
                logger.error(f"Error storing in master_docs: {str(e)}")
                # Don't fail the entire process if master collection fails
            
            # ═══════════════════════════════════════════════════════════════════════════
            # Step 10: Mark document as processed
            # ═══════════════════════════════════════════════════════════════════════════
            try:
                document.processed = True
                db.commit()
                
                # Update upload log
                if upload_log:
                    upload_log.processing_completed = True
                    upload_log.processed_at = datetime.utcnow()
                    db.commit()
                
                logger.info(
                    f"✓ Successfully processed document {document_id} "
                    f"({len(chunks)} chunks, {extraction_method}) for admin {admin_id}"
                )
            except Exception as e:
                logger.error(f"Error marking document as processed: {str(e)}")
        
        except Exception as e:
            logger.error(f"Error processing document {document_id}: {str(e)}", exc_info=True)
            # Update upload log with error
            try:
                upload_log = db.query(DocumentUploadLog).filter(
                    DocumentUploadLog.document_id == document_id
                ).first()
                if upload_log:
                    upload_log.processing_error = str(e)[:500]  # Limit error message length
                    upload_log.processing_started = True
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

