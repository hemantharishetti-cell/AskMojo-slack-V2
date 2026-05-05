"""
OCR Pipeline Orchestrator.

Coordinates the full flow: PDF rendering → page-level OCR → OpenAI structuring.
This is the main entry-point for the OCR pipeline.
"""

import gc
import logging
import time
import os
import tempfile
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path
from PIL import Image

from app.ocr_pipeline.config import DPI, PDF_WORKERS, RENDER_BATCH_SIZE
from app.ocr_pipeline.ocr_engine import get_ocr, process_page
from app.ocr_pipeline.structuring import structure_pages

logger = logging.getLogger(__name__)


def run_ocr_pipeline(pdf_path: str | Path) -> dict:
    """
    Run the full OCR pipeline on a PDF file with batch processing.

    Steps:
        1. Render and process PDF pages in batches (5 pages at a time).
        2. Run PaddleOCR on each page (with retry on low confidence).
        3. Send raw text to OpenAI for heading extraction and chunking.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Dict with 'metadata' and 'chunks' keys (StructuredDocument format).
        If no text is extracted, 'chunks' will be an empty list.
    """
    pdf_path = Path(pdf_path)
    start_time = time.time()
    logger.info(f"🔄 OCR Pipeline: Processing {pdf_path.name}")

    # ── Step 1: Get page count ──────────────────────────────────────────────
    info = pdfinfo_from_path(str(pdf_path))
    total_pages = int(info.get("Pages", 0))
    logger.info(f"📄 Total pages: {total_pages} | Processing in batches at {DPI} DPI...")

    # ── Step 2: Process pages in batches to manage memory ───────────────────
    BATCH_SIZE = 1  # Process 1 page at a time for minimal memory footprint
    ocr_pages = []
    total_batches = (total_pages + BATCH_SIZE - 1) // BATCH_SIZE
    
    get_ocr()  # warm up the model
    ocr_start = time.time()
    
    # Use a temp directory to write rendered pages and process them one-by-one
    with tempfile.TemporaryDirectory(prefix="ocr_pages_") as tmpdir:
        for batch_num in range(total_batches):
            batch_start = batch_num * BATCH_SIZE + 1  # pdf2image uses 1-based indexing
            batch_end = min((batch_num + 1) * BATCH_SIZE, total_pages)

            logger.info(f"  Batch {batch_num + 1}/{total_batches}: Processing pages {batch_start}-{batch_end}...")

            try:
                pdf_render_start = time.time()
                page_paths = convert_from_path(
                    str(pdf_path),
                    dpi=DPI,
                    thread_count=PDF_WORKERS,
                    first_page=batch_start,
                    last_page=batch_end,
                    output_folder=tmpdir,
                    fmt="png",
                    paths_only=True,
                )
                pdf_render_time = time.time() - pdf_render_start

                batch_ocr_start = time.time()
                for page_idx, page_path in enumerate(page_paths):
                    global_page_num = batch_start + page_idx - 1
                    with Image.open(page_path) as img:
                        ocr_result = process_page(global_page_num, img)
                    ocr_pages.append(ocr_result)
                    try:
                        os.remove(page_path)
                    except Exception:
                        pass
                    gc.collect()
                batch_ocr_time = time.time() - batch_ocr_start

                logger.info(
                    f"    ✓ Batch {batch_num + 1} done: {len(page_paths)} pages in {pdf_render_time + batch_ocr_time:.1f}s"
                )

                gc.collect()

            except MemoryError as e:
                logger.error(f"❌ Out of memory while processing batch {batch_num + 1}: {e}")
                raise
            except Exception as e:
                logger.error(f"❌ Error processing batch {batch_num + 1}: {e}")
                raise
    
    ocr_time = time.time() - ocr_start
    logger.info(f"  OCR: {ocr_time:.2f}s for {len(ocr_pages)} pages ({total_batches} batches)")

    # ── Step 3: Check if we extracted any text ──────────────────────────────
    non_empty = [p for p in ocr_pages if p["raw_text"].strip()]
    if not non_empty:
        logger.warning("⚠️ No text extracted from any page.")
        return {
            "metadata": {
                "document_title": pdf_path.stem.replace("-", " ").replace("_", " ").title(),
                "total_pages": total_pages,
                "processed_pages": 0,
            },
            "chunks": [],
        }

    # ── Step 4: OpenAI structuring ──────────────────────────────────────────
    doc_title = pdf_path.stem.replace("-", " ").replace("_", " ").title()
    ai_start = time.time()
    
    # A. First pass: Document-level metadata extraction (from first 2 pages)
    logger.info("📑 Extracting document-level metadata...")
    from app.ocr_pipeline.structuring import extract_document_metadata
    global_meta = extract_document_metadata(ocr_pages, doc_title)
    
    # B. Second pass: Detailed chunk structuring
    logger.info("🧩 Structuring chunks...")
    structured = structure_pages(ocr_pages, doc_title, total_pages, global_meta=global_meta)
    ai_time = time.time() - ai_start

    total_time = time.time() - start_time
    chunk_count = len(structured.get("chunks", []))
    logger.info(
        f"✅ OCR Pipeline complete: {chunk_count} chunks | "
        f"ocr={ocr_time:.1f}s  ai={ai_time:.1f}s  total={total_time:.1f}s"
    )

    return structured
