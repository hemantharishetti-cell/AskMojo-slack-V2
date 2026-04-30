"""
OCR Pipeline Module

PaddleOCR + OpenAI structuring pipeline for PDF text extraction and chunking.
Replaces Adobe PDF Extract API + StructuredChunkerV2.
"""

from app.ocr_pipeline.pipeline import run_ocr_pipeline
from app.ocr_pipeline.adapter import convert_ocr_chunks_for_chromadb

__all__ = ["run_ocr_pipeline", "convert_ocr_chunks_for_chromadb"]
