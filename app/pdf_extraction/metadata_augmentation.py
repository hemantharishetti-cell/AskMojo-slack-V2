"""
Metadata Augmentation for PDF Chunks

Enriches chunks with additional metadata for better retrieval and semantics.
"""

import logging
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
        doc_type: str,
        domain: str = None,
    ) -> List[Dict[str, Any]]:
        """
        Add metadata to chunks for ChromaDB storage.
        
        Args:
            chunks: List of chunks to augment
            document_id: Document ID
            document_title: Document title
            category: Category name
            doc_type: Document type (proposal, case_study, solution, etc.)
            domain: Optional domain name
            
        Returns:
            Augmented chunks list
        """
        augmented = []
        
        for chunk in chunks:
            augmented_chunk = chunk.copy()
            
            # Add document metadata
            augmented_chunk["document_id"] = document_id
            augmented_chunk["document_title"] = document_title
            augmented_chunk["category"] = category
            augmented_chunk["doc_type"] = doc_type
            
            # Always include domain and document title in metadata
            augmented_chunk["domain"] = domain if domain else ""
            augmented_chunk["document_title"] = document_title if document_title else ""
            
            # Build section path (for filtering)
            section_path = []
            if chunk.get("heading_level_1"):
                section_path.append(chunk["heading_level_1"])
            if chunk.get("heading_level_2"):
                section_path.append(chunk["heading_level_2"])
            if chunk.get("heading_level_3"):
                section_path.append(chunk["heading_level_3"])
            
            augmented_chunk["section_path"] = " > ".join(section_path) if section_path else None
            
            # Add extraction metadata
            augmented_chunk["extraction_date"] = datetime.utcnow().isoformat()
            augmented_chunk["extraction_version"] = 1
            
            # Add retrievability flags without relying on element_type
            is_heading = bool(
                chunk.get("heading_level_1") or chunk.get("heading_level_2") or chunk.get("heading_level_3")
            )
            is_table = chunk.get("is_table", False)
            # Heuristic list detection based on bullet markers in text
            text_val = (chunk.get("text") or "").lstrip()
            is_list = text_val.startswith("• ") or "\n• " in text_val

            augmented_chunk["is_heading"] = is_heading
            augmented_chunk["is_table"] = is_table
            augmented_chunk["is_list"] = is_list
            
            # Add readability score (tables/lists might need special handling)
            augmented_chunk["readability_score"] = MetadataAugmentation._calculate_readability(chunk.get("text", ""))
            
            augmented.append(augmented_chunk)
        
        logger.info(f"Augmented {len(augmented)} chunks with metadata for document {document_id}")
        return augmented
    
    @staticmethod
    def _calculate_readability(text: str) -> float:
        """
        Calculate a simple readability score (0-1).
        
        Args:
            text: Text to score
            
        Returns:
            Readability score
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
        
        ChromaDB requires flat key-value pairs for filtering.
        
        Args:
            chunk: Augmented chunk
            
        Returns:
            ChromaDB-compatible metadata dict
        """
        metadata = {
            "document_id": str(chunk.get("document_id", "")),
            "document_title": chunk.get("document_title", ""),
            "category": chunk.get("category", ""),
            "doc_type": chunk.get("doc_type", ""),
            "chunk_index": str(chunk.get("chunk_index", 0)),
            "extraction_source": chunk.get("extraction_source", "adobe_api"),
            "element_type": chunk.get("element_type", "paragraph"),
            "is_heading": str(chunk.get("is_heading", False)).lower(),
            "is_table": str(chunk.get("is_table", False)).lower(),
            "is_list": str(chunk.get("is_list", False)).lower(),
        }
        
        # Add optional fields
        if chunk.get("page_number"):
            metadata["page_number"] = str(chunk["page_number"])
        
        if chunk.get("section"):
            metadata["section"] = chunk["section"]
        
        if chunk.get("heading_level_1"):
            metadata["heading_1"] = chunk["heading_level_1"]
        
        if chunk.get("heading_level_2"):
            metadata["heading_2"] = chunk["heading_level_2"]
        
        if chunk.get("heading_level_3"):
            metadata["heading_3"] = chunk["heading_level_3"]
        
        if chunk.get("domain"):
            metadata["domain"] = chunk["domain"]
        
        if chunk.get("section_path"):
            metadata["section_path"] = chunk["section_path"]
        
        return metadata
