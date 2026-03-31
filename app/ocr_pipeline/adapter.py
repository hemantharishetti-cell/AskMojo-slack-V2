"""
Adapter: converts OCR pipeline chunks to the ChromaDB chunk format
expected by the existing vector_logic pipeline.

The OCR pipeline produces chunks with fields:
    chunk_id, h1, h2, content, keywords, metadata.{page_number, chunk_index, content_type, confidence}

The existing pipeline expects chunks with fields:
    text, page_number, chunk_index, heading_level_1, heading_level_2, heading_level_3,
    section, is_table, document_id, version
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


def convert_ocr_chunks_for_chromadb(
    ocr_output: dict,
    document_id: int,
    document_description: dict | None = None,
) -> List[Dict[str, Any]]:
    """
    Convert OCR pipeline output to the chunk format expected by
    ``store_chunks_in_chromadb`` and ``app.ocr_pipeline.metadata.MetadataAugmentation``.

    Args:
        ocr_output: Dict returned by ``run_ocr_pipeline()`` with 'metadata' and 'chunks'.
        document_id: The document ID from SQLite.

    Returns:
        List of chunk dicts compatible with the existing vector pipeline.
    """
    raw_chunks = ocr_output.get("chunks", [])
    if not raw_chunks:
        logger.warning(f"[OCR ADAPTER] No chunks to convert for document {document_id}")
        return []

    converted: List[Dict[str, Any]] = []
    global_meta = ocr_output.get("metadata", {})

    for chunk in raw_chunks:
        meta = chunk.get("metadata", {})
        h1 = chunk.get("h1", "") or ""
        h2 = chunk.get("h2", "") or ""
        content = chunk.get("content", "") or ""
        keywords = chunk.get("keywords", []) or []

        # Build the text field: content is primary, append keywords for better retrieval
        text_parts = [content]
        if keywords:
            # Ensure all keywords are strings to avoid TypeError in join
            safe_keywords = [str(k) for k in keywords if k]
            if safe_keywords:
                text_parts.append("Keywords: " + ", ".join(safe_keywords))
        text = "\n\n".join(part for part in text_parts if part.strip())

        # Build section path (mirrors StructuredChunkerV2 format)
        section = meta.get("section_path")
        if not section:
            h1_str = str(h1) if h1 is not None else ""
            h2_str = str(h2) if h2 is not None else ""
            if h1_str:
                section = h1_str
                if h2_str:
                    section = f"{h1_str} > {h2_str}"
            elif h2_str:
                section = h2_str

        converted_chunk: Dict[str, Any] = {
            "chunk_index": meta.get("chunk_index", len(converted) + 1),
            "page_number": meta.get("page_number"),
            "text": text,
            "heading_level_1": h1 or None,
            "heading_level_2": h2 or None,
            "heading_level_3": None,  # OCR pipeline doesn't produce h3
            "section": section,
            "is_table": meta.get("is_table", (meta.get("content_type", "") == "table")),
            "document_id": document_id,
            # Document-level global fields
            "doc_type": global_meta.get("doc_type", "other"),
            "domain": global_meta.get("domain", ""),
            "solution_area": global_meta.get("solution_area", ""),
            "industry": global_meta.get("industry", ""),
            "technology": global_meta.get("technology", ""),
            "use_case": global_meta.get("use_case", ""),
            "entity": global_meta.get("entity", "internal"),
            "created_at": global_meta.get("created_at"),
            # Extraction-level fields
            "ocr_chunk_id": chunk.get("chunk_id"),
            "ocr_confidence": meta.get("confidence"),
            "ocr_content_type": meta.get("ocr_content_type") or meta.get("content_type"),
        }

        # ── Step 6.6: Integrate 16-attribute structured description ─────────
        # This metadata flows into ChromaDB for every chunk, enabling rich routing.
        if document_description:
            # List of the 16 standard keys we want to store
            description_keys = [
                "PRIMARY_ENTITY", "DOCUMENT_DOMAIN", "INDUSTRY_OR_SECTOR", 
                "GEOGRAPHIC_FOCUS", "DOCUMENT_TYPE_OR_PURPOSE", "MAIN_TOPICS", 
                "KEY_ENTITIES", "TIMELINE_OR_PHASES", "BUSINESS_PROBLEMS", 
                "SOLUTIONS_OR_METHODS", "TOOLS_AND_TECHNOLOGIES", "BENEFITS_AND_OUTCOMES", 
                "ROLES_AND_RESPONSIBILITIES", "CHALLENGES_OR_GAPS", "KEYWORDS", "QUERY_MATCH_SIGNALS"
            ]
            for key in description_keys:
                val = document_description.get(key)
                if val:
                    # Capture for storage; MetadataAugmentation.create_chromadb_metadata 
                    # will later flatten/format this for ChromaDB compatibility.
                    converted_chunk[key] = val

        converted.append(converted_chunk)

    logger.info(f"[OCR ADAPTER] Converted {len(converted)} chunks for document {document_id}")
    return converted
