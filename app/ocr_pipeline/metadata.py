"""
Metadata Augmentation for PDF Chunks

Enriches chunks with additional metadata for better retrieval and semantics.
"""

import logging
import time
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class MetadataAugmentation:
    """
    Augments chunks with rich metadata for ChromaDB storage.
    """
    
    @staticmethod
    def augment_chunks(
        chunks: List[Dict[str, Any]],
        document_id: int,
        document_title: str,
        category: str,
        doc_type: str = "other",
        domain: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Add metadata to chunks for ChromaDB storage.
        """
        augmented = []
        
        for chunk in chunks:
            augmented_chunk = chunk.copy()
            
            # 1. Base Document Identity
            augmented_chunk["document_id"] = document_id
            augmented_chunk["document_title"] = document_title or augmented_chunk.get("document_title", "")
            augmented_chunk["category"] = category or augmented_chunk.get("category", "")
            
            # 2. Priority: Use LLM-extracted doc_type/domain if present in chunk, else use args
            augmented_chunk["doc_type"] = augmented_chunk.get("doc_type") or doc_type or "other"
            augmented_chunk["domain"] = augmented_chunk.get("domain") or domain or ""
            
            # 3. Add system audit metadata
            augmented_chunk["extraction_date"] = datetime.utcnow().isoformat()
            augmented_chunk["extraction_version"] = 2
            
            # 4. Content Flags
            text_val = (chunk.get("text") or "").lstrip()
            is_list = text_val.startswith("• ") or "\n• " in text_val
            augmented_chunk["is_list"] = is_list
            
            # 5. Readability score
            augmented_chunk["readability_score"] = MetadataAugmentation._calculate_readability(chunk.get("text", ""))
            
            augmented.append(augmented_chunk)
        
        logger.info(f"Augmented {len(augmented)} chunks with standard 12-field schema for document {document_id}")
        return augmented

    @staticmethod
    def _calculate_readability(text: str) -> float:
        """
        Calculate a simple readability score (0-1).
        """
        if not text:
            return 0.0
        
        # Simple heuristic: higher word count = better readability (up to a point)
        word_count = len(text.split())
        
        # Ideal chunk is 100-500 words
        if word_count < 50:
            return word_count / 50.0 * 0.7  # 0.7 points max
        elif word_count <= 500:
            return 0.9
        else:
            return 0.5  # Large chunks are less readable

    @staticmethod
    def create_chromadb_metadata(chunk: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create ChromaDB-specific metadata dict from chunk.
        Maps the production 12-field schema to flat, queryable attributes.
        """
        raw_doc_id = chunk.get("document_id")
        doc_id_str = str(raw_doc_id or "")
        try:
            doc_id_int = int(raw_doc_id) if raw_doc_id is not None and str(raw_doc_id).strip() != "" else None
        except (TypeError, ValueError):
            doc_id_int = None

        # Build basic metadata map
        metadata = {
            # Keep both keys for compatibility:
            # - `doc_id` is used by current retrieval and delete paths
            # - `document_id` supports legacy filters and admin tooling
            "doc_id": doc_id_str,
            "chunk_index": int(chunk.get("chunk_index", 0)),
            
            "doc_type": chunk.get("doc_type", "other"),
            "domain": chunk.get("domain", ""),
            "solution_area": chunk.get("solution_area", ""),
            "industry": chunk.get("industry", ""),
            
            "entity": chunk.get("entity", "internal"),
            "technology": chunk.get("technology", ""),
            "use_case": chunk.get("use_case", ""),
            
            "is_table": 1 if chunk.get("is_table") else 0,
            "ocr_confidence": float(chunk.get("ocr_confidence") if chunk.get("ocr_confidence") is not None else 1.0),
            "created_at": int(chunk.get("created_at") if chunk.get("created_at") is not None else time.time()),
        }

        # ── Step 6.7: Flatten 16 structured attributes for ChromaDB ─────────
        # We ensure all fields are flat (strings or ints) for ChromaDB compatibility.
        # String fields are capped at 200 chars; arrays are comma-joined.
        rich_keys = [
            "PRIMARY_ENTITY", "DOCUMENT_DOMAIN", "INDUSTRY_OR_SECTOR", 
            "GEOGRAPHIC_FOCUS", "DOCUMENT_TYPE_OR_PURPOSE", "MAIN_TOPICS", 
            "KEY_ENTITIES", "TIMELINE_OR_PHASES", "BUSINESS_PROBLEMS", 
            "SOLUTIONS_OR_METHODS", "TOOLS_AND_TECHNOLOGIES", "BENEFITS_AND_OUTCOMES", 
            "ROLES_AND_RESPONSIBILITIES", "CHALLENGES_OR_GAPS", "KEYWORDS", "QUERY_MATCH_SIGNALS"
        ]
        for key in rich_keys:
            val = chunk.get(key)
            if val:
                if isinstance(val, list):
                    # For keywords and topics, join into a searchable string
                    metadata[key.lower()] = ", ".join(str(v) for v in val if v)[:500]
                elif isinstance(val, str):
                    metadata[key.lower()] = val.strip()[:500]
                else:
                    metadata[key.lower()] = str(val)[:500]

        if doc_id_int is not None:
            metadata["document_id"] = doc_id_int

        # Add string fields with 200-char cap for terminal safety / ChromaDB limits
        for field in ("section", "heading_level_1", "heading_level_2", "ocr_content_type"):
            val = chunk.get(field)
            if val and isinstance(val, str) and val.strip():
                # Use 'section_path' as the key for ChromaDB (mapped from 'section')
                if field == "section":
                    metadata["section_path"] = val.strip()[:200]
                else:
                    metadata[field] = val.strip()[:200]

        # Fallback for section_path if section was missing
        if "section_path" not in metadata:
            metadata["section_path"] = str(chunk.get("section_path", ""))[:200]
        try:
            pg = chunk.get("page_number")
            if pg is not None:
                if isinstance(pg, (int, float)):
                    metadata["page_number"] = int(pg)
                elif isinstance(pg, str) and pg.isdigit():
                    metadata["page_number"] = int(pg)
        except (ValueError, TypeError):
            pass
        
        return metadata
