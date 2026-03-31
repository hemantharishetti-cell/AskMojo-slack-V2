"""
OCR Pipeline Orchestrator.

Coordinates the full flow: PDF rendering → page-level OCR → OpenAI structuring.
This is the main entry-point for the OCR pipeline.
"""

import logging
import time
from pathlib import Path

from pdf2image import convert_from_path, pdfinfo_from_path

from app.ocr_pipeline.config import DPI, PDF_WORKERS
from app.ocr_pipeline.ocr_engine import get_ocr, process_page
from app.ocr_pipeline.structuring import structure_pages

logger = logging.getLogger(__name__)


def run_ocr_pipeline(pdf_path: str | Path) -> dict:
    """
    Run the full OCR pipeline on a PDF file.

    Steps:
        1. Render PDF pages to images.
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

    # ── Step 1: Get page count and render ─────────────────────────────────
    info = pdfinfo_from_path(str(pdf_path))
    total_pages = int(info.get("Pages", 0))
    logger.info(f"📄 Rendering {total_pages} pages at {DPI} DPI...")

    pdf_render_start = time.time()
    pages = convert_from_path(str(pdf_path), dpi=DPI, thread_count=PDF_WORKERS)
    pdf_render_time = time.time() - pdf_render_start
    logger.info(f"  PDF render: {pdf_render_time:.2f}s")

    # ── Step 2: OCR each page ─────────────────────────────────────────────
    get_ocr()  # warm up the model
    ocr_start = time.time()
    ocr_pages = [process_page(i, page) for i, page in enumerate(pages)]
    ocr_time = time.time() - ocr_start
    logger.info(f"  OCR: {ocr_time:.2f}s for {len(ocr_pages)} pages")

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

    # ── Step 3: OpenAI structuring ────────────────────────────────────────
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
        f"render={pdf_render_time:.1f}s  ocr={ocr_time:.1f}s  ai={ai_time:.1f}s  total={total_time:.1f}s"
    )

    return structured
