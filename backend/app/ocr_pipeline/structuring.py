"""
OpenAI-based document structuring.

Takes raw OCR text pages and uses GPT to structure them into
headed, chunked JSON with h1/h2, content, and keywords.
"""

import json
import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from app.ocr_pipeline.config import BATCH_SIZE, MAX_WORKERS, MODEL_NAME
from app.ocr_pipeline.models import StructuredDocument
from app.ocr_pipeline.text_utils import clean_text

logger = logging.getLogger(__name__)

# ── OpenAI client singleton ──────────────────────────────────────────────────
_opened_openai_client = None


def get_openai_client():
    """Return a lazily-initialized OpenAI client."""
    global _opened_openai_client
    if _opened_openai_client is None:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("❌ OPENAI_API_KEY is not set.")
        _opened_openai_client = OpenAI(api_key=api_key)
    return _opened_openai_client


def count_tokens(text: str) -> int:
    """Count tokens for the configured model."""
    import tiktoken

    encoding = tiktoken.encoding_for_model(MODEL_NAME)
    return len(encoding.encode(text))


# ── JSON Schema for structured output ────────────────────────────────────────
SCHEMA = {
    "type": "object",
    "properties": {
        "metadata": {
            "type": "object",
            "properties": {
                "document_title": {"type": "string"},
                "total_pages": {"type": "integer"},
                "processed_pages": {"type": "integer"},
            },
            "required": ["document_title", "total_pages", "processed_pages"],
            "additionalProperties": False,
        },
        "document_description": {
            "type": "object",
            "properties": {
                "PRIMARY_ENTITY": {"type": "string"},
                "DOCUMENT_DOMAIN": {"type": "string"},
                "INDUSTRY_OR_SECTOR": {"type": "string"},
                "GEOGRAPHIC_FOCUS": {"type": "string"},
                "DOCUMENT_TYPE_OR_PURPOSE": {"type": "string"},
                "MAIN_TOPICS": {"type": "array", "items": {"type": "string"}},
                "KEY_ENTITIES": {"type": "array", "items": {"type": "string"}},
                "TIMELINE_OR_PHASES": {"type": "string"},
                "BUSINESS_PROBLEMS": {"type": "array", "items": {"type": "string"}},
                "SOLUTIONS_OR_METHODS": {"type": "array", "items": {"type": "string"}},
                "TOOLS_AND_TECHNOLOGIES": {"type": "array", "items": {"type": "string"}},
                "BENEFITS_AND_OUTCOMES": {"type": "array", "items": {"type": "string"}},
                "ROLES_AND_RESPONSIBILITIES": {"type": "array", "items": {"type": "string"}},
                "CHALLENGES_OR_GAPS": {"type": "array", "items": {"type": "string"}},
                "KEYWORDS": {"type": "array", "items": {"type": "string"}},
                "ENUMERATED_CONTENT": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "PRIMARY_ENTITY", "DOCUMENT_DOMAIN", "INDUSTRY_OR_SECTOR", "GEOGRAPHIC_FOCUS",
                "DOCUMENT_TYPE_OR_PURPOSE", "MAIN_TOPICS", "KEY_ENTITIES", "TIMELINE_OR_PHASES",
                "BUSINESS_PROBLEMS", "SOLUTIONS_OR_METHODS", "TOOLS_AND_TECHNOLOGIES",
                "BENEFITS_AND_OUTCOMES", "ROLES_AND_RESPONSIBILITIES", "CHALLENGES_OR_GAPS",
                "KEYWORDS", "ENUMERATED_CONTENT"
            ],
            "additionalProperties": False,
        },
        "chunks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "h1": {"type": "string"},
                    "h2": {"type": "string"},
                    "content": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "page_number": {"type": "integer"},
                            "chunk_index": {"type": "integer"},
                            "content_type": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                        "required": ["page_number", "chunk_index", "content_type", "confidence"],
                        "additionalProperties": False,
                    },
                },
                "required": ["chunk_id", "h1", "h2", "content", "keywords", "metadata"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["metadata", "chunks", "document_description"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "You are a document structuring engine. Your task is to extract meaning from the raw OCR text.\n"
    "Rules:\n"
    "1. Convert raw text into structured JSON chunks. No hallucination, no invented text, only reorganize.\n"
    "2. Split large text into coherent chunks based on headings and logical flow. Maintain exact wording.\n"
    "3. GENERATE A SUMMARY (document_description) using the provided JSON schema. This summary should clearly "
    "   document the 'PRIMARY_ENTITY', 'DOCUMENT_DOMAIN', 'KEY_ENTITIES', 'MAIN_TOPICS', etc. extracted from this batch.\n"
    "4. Return ONLY valid JSON matching the schema."
)


# ── Batch processing ────────────────────────────────────────────────────────

def process_batch(batch: list, document_title: str, total_pages: int):
    """
    Send a batch of OCR pages to OpenAI for structuring.

    Returns:
        (structured_dict, tokens_before, tokens_after)
    """
    payload = []
    t_before, t_after = 0, 0

    for page in batch:
        raw = page["raw_text"]
        if not raw.strip():
            continue
        cleaned = clean_text(raw)
        t_before += count_tokens(raw)
        t_after += count_tokens(cleaned)
        payload.append({"page_number": page["page_number"], "raw_text": cleaned})

    if not payload:
        return None, 0, 0

    client = get_openai_client()
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "doc", "strict": True, "schema": SCHEMA},
                },
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps({"title": document_title, "pages": payload}),
                    },
                ],
            )
            raw_result = json.loads(resp.choices[0].message.content)

            # Ensure unique chunk IDs
            for chunk in raw_result.get("chunks", []):
                chunk["chunk_id"] = str(uuid.uuid4())
                if "metadata" not in chunk:
                    chunk["metadata"] = {}

            return raw_result, t_before, t_after
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


# ── Document Metadata Extraction (NEW) ──────────────────────────────────

DOC_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["presales", "solution", "case_study", "policy", "other"]},
        "domain": {"type": "string"},
        "solution_area": {"type": "string"},
        "industry": {"type": "string"},
        "technology": {"type": "string"},
        "use_case": {"type": "string"},
        "entity": {"type": "string"},
        "created_at_year": {"type": "integer"},
    },
    "required": ["doc_type", "domain", "solution_area", "industry", "technology", "use_case", "entity", "created_at_year"],
    "additionalProperties": False,
}

DOC_METADATA_PROMPT = (
    "You are a document analyzer. Review the following first few pages of a document "
    "and extract global metadata attributes.\n"
    "Rule for 'entity': If it's a proprietary internal document, use 'internal'. "
    "Otherwise, name the client or organization it concerns (e.g., 'TechFin Bank').\n"
    "Rule for 'doc_type': Choose 'presales', 'solution', 'case_study', 'policy', or 'other'.\n"
    "Return ONLY valid JSON."
)


def extract_document_metadata(ocr_pages: list, document_title: str) -> dict:
    """
    Extract document-level metadata from the first 2 pages.
    """
    if not ocr_pages:
        return {}

    # Extract text from first 2 pages (or fewer if doc is short)
    sample_text = ""
    for page in ocr_pages[:2]:
        sample_text += clean_text(page["raw_text"]) + "\n"

    try:
        client = get_openai_client()
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "doc_meta", "strict": True, "schema": DOC_METADATA_SCHEMA},
            },
            messages=[
                {"role": "system", "content": DOC_METADATA_PROMPT},
                {"role": "user", "content": f"Title: {document_title}\n\nContent:\n{sample_text[:4000]}"},
            ],
        )
        result = json.loads(resp.choices[0].message.content)
        
        # Convert created_at_year to unix timestamp if possible (simplistic)
        year = result.pop("created_at_year", 0)
        from datetime import datetime
        try:
            if 1900 < year < 2100:
                dt = datetime(year, 1, 1)
                result["created_at"] = int(dt.timestamp())
            else:
                result["created_at"] = int(time.time())
        except Exception:
            result["created_at"] = int(time.time())
            
        return result
    except Exception as e:
        logger.error(f"❌ Document metadata extraction failed: {e}")
        return {
            "doc_type": "other",
            "domain": "",
            "solution_area": "",
            "industry": "",
            "technology": "",
            "use_case": "",
            "entity": "internal",
            "created_at": int(time.time())
        }


def structure_pages(ocr_pages: list, document_title: str, total_pages: int, global_meta: dict = None) -> dict:
    """
    Structure all OCR pages into a single StructuredDocument dict.

    Args:
        ocr_pages: List of dicts with 'page_number' and 'raw_text'.
        document_title: Human-friendly title derived from the filename.
        total_pages: Total number of pages in the PDF.
        global_meta: Extracted document-level metadata (doc_type, domain, etc.).

    Returns:
        Dict with 'metadata' and 'chunks' keys.
    """
    non_empty = [p for p in ocr_pages if p["raw_text"].strip()]
    if not non_empty:
        logger.warning("No text extracted from any page — skipping AI structuring.")
        base_meta = {"document_title": document_title, "total_pages": total_pages, "processed_pages": 0}
        if global_meta:
            base_meta.update(global_meta)
        return {"metadata": base_meta, "chunks": []}

    logger.info(f"🧠 Structuring {len(non_empty)} pages with OpenAI ({MODEL_NAME})...")
    batches = [ocr_pages[i : i + BATCH_SIZE] for i in range(0, len(ocr_pages), BATCH_SIZE)]
    results = {}
    t_before, t_after = 0, 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
        futures = {exe.submit(process_batch, b, document_title, total_pages): idx for idx, b in enumerate(batches)}
        for f in as_completed(futures):
            res, b, a = f.result()
            if res:
                results[futures[f]] = res
            t_before += b
            t_after += a

    # Merge batch results in order
    ordered = [results[i] for i in sorted(results)]
    all_chunks = []
    merged_description = {}
    
    for batch_result in ordered:
        all_chunks.extend(batch_result.get("chunks", []))
        # Keep the description from the first batch that has meaningful content
        if not merged_description and batch_result.get("document_description"):
            merged_description = batch_result.get("document_description")

    # Re-index chunks globally and enrich with metadata
    for i, c in enumerate(all_chunks):
        if "metadata" in c:
            m = c["metadata"]
            m["chunk_index"] = i + 1
            
            # Enrich with derived fields
            h1 = c.get("h1", "")
            h2 = c.get("h2", "")
            m["section_path"] = f"{h1} > {h2}" if h1 and h2 else (h1 or h2)
            m["is_table"] = 1 if m.get("content_type") == "table" else 0

    final_metadata = {
        "document_title": document_title,
        "total_pages": total_pages,
        "processed_pages": len(non_empty),
    }
    if global_meta:
        final_metadata.update(global_meta)

    final_output = {
        "metadata": final_metadata,
        "chunks": all_chunks,
        "document_description": merged_description,
    }

    logger.info(f"✅ Structuring complete: {len(all_chunks)} chunks from {len(non_empty)} pages (tokens: {t_before} → {t_after})")
    return final_output
