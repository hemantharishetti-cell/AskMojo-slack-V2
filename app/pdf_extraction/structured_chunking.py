"""
Structured Chunking from Adobe PDF Extract API

Parses Adobe's JSON output and creates intelligent, section-aware chunks
while preserving heading hierarchy and document structure.

DEPRECATION NOTICE:
This module is deprecated in favor of `structured_chunker_v2.py` (deterministic,
heading-anchored chunking). The file is kept for backward-compatibility and
reference. New processing uses `StructuredChunkerV2`.
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
import re

logger = logging.getLogger(__name__)
logger.warning("[CHUNKER] Deprecated module loaded: prefer structured_chunker_v2.py")


class StructuredChunker:
    """
    Parse Adobe PDF Extract API JSON and create semantic chunks.
    
    Respects document structure:
    - Heading hierarchy (H1 -> H2 -> H3)
    - Section boundaries
    - Content grouping
    - Table preservation
    """
    
    MIN_CHUNK_SIZE = 100  # Minimum characters for a chunk
    MAX_CHUNK_SIZE = 1000  # Maximum characters for a chunk
    
    @staticmethod
    def chunk_adobe_json(
        adobe_json: Dict[str, Any],
        page_numbers: Optional[Dict[str, int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Parse Adobe JSON and create chunks.
        
        Args:
            adobe_json: Response from Adobe Extract API
            page_numbers: Optional mapping of text snippets to page numbers
            
        Returns:
            List of chunks with metadata
        """
        chunks = []
        elements = adobe_json.get("elements", [])
        
        logger.info(f"[CHUNKER] Starting Adobe JSON processing: {len(elements)} elements found")
        
        # Debug: Log first element to verify structure
        if elements:
            logger.debug(f"[CHUNKER] First element keys: {list(elements[0].keys())}")
            logger.debug(f"[CHUNKER] First element: {elements[0]}")
        
        chunk_buffer = {
            "text": "",
            "heading_1": None,
            "heading_2": None,
            "heading_3": None,
            "page_number": None,
            "element_types": [],
            "section": None,
        }
        
        for idx, element in enumerate(elements):
            chunk_buffer = StructuredChunker._process_element(
                element,
                chunk_buffer,
                chunks,
                page_numbers=page_numbers
            )
        
        # Flush remaining content
        if chunk_buffer["text"].strip():
            chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
        
        logger.info(f"[CHUNKER] Created {len(chunks)} chunks from {len(elements)} Adobe elements")
        
        # Log chunk statistics
        if chunks:
            total_chars = sum(c["char_count"] for c in chunks)
            avg_chars = total_chars / len(chunks)
            total_words = sum(c["word_count"] for c in chunks)
            
            logger.info(f"[CHUNKER] Chunk statistics:")
            logger.info(f"  - Total characters: {total_chars}")
            logger.info(f"  - Average chunk size: {avg_chars:.0f} chars")
            logger.info(f"  - Total words: {total_words}")
            
            # Log chunk type breakdown
            chunk_types = {}
            for chunk in chunks:
                types = chunk.get("element_types", [])
                for t in types:
                    chunk_types[t] = chunk_types.get(t, 0) + 1
            
            if chunk_types:
                logger.info(f"[CHUNKER] Chunk composition: {chunk_types}")
            
            # Log first 3 chunks as samples
            logger.info("[CHUNKER] Sample chunks:")
            for chunk in chunks[:3]:
                text_preview = chunk["text"][:80].replace("\n", " ")
                section = chunk.get("section", "N/A")
                page = chunk.get("page_number", "N/A")
                logger.info(f"  Chunk #{chunk['chunk_index']} (Page {page}, {chunk['char_count']} chars): {text_preview}...")
        
        return chunks
    
    @staticmethod
    def _process_element(
        element: Dict[str, Any],
        chunk_buffer: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        level: int = 0,
        path: List[str] = None,
        page_numbers: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """
        Recursively process element from Adobe JSON.
        
        Args:
            element: Element from Adobe JSON
            chunk_buffer: Current chunk being built
            chunks: List of completed chunks
            level: Current nesting level
            path: Path of section hierarchy
            page_numbers: Optional page number mapping
            
        Returns:
            Updated chunk_buffer
        """
        if path is None:
            path = []
        
        # Adobe returns content in "Text" (capital T), not "text"
        text = element.get("Text", "").strip()
        
        # Extract element type from "Path" field (e.g., "//Document/H1" -> "H1")
        adobe_path = element.get("Path", "").lower()
        element_type = ""
        if "/h1" in adobe_path:
            element_type = "h1"
        elif "/h2" in adobe_path:
            element_type = "h2"
        elif "/h3" in adobe_path:
            element_type = "h3"
        elif "/p" in adobe_path:
            element_type = "p"
        elif "/table" in adobe_path:
            element_type = "table"
        elif "/list" in adobe_path or "/li" in adobe_path:
            element_type = "list"
        
        # Get page number from Adobe's "Page" field
        page_number = element.get("Page")
        if page_number is not None and chunk_buffer["page_number"] is None:
            chunk_buffer["page_number"] = page_number
        
        if element_type:
            logger.debug(f"[CHUNKER] Processing {element_type}: {text[:50]}")
        
        
        # Handle headings
        if element_type.startswith("h"):
            # Flush current chunk if it has content
            if chunk_buffer["text"].strip():
                chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
                chunk_buffer = {
                    "text": "",
                    "heading_1": chunk_buffer.get("heading_1"),
                    "heading_2": chunk_buffer.get("heading_2"),
                    "heading_3": chunk_buffer.get("heading_3"),
                    "page_number": chunk_buffer.get("page_number"),
                    "element_types": [],
                    "section": chunk_buffer.get("section"),
                }
            
            # Update heading hierarchy
            heading_level = int(element_type[1]) if len(element_type) > 1 else 1
            if heading_level == 1:
                chunk_buffer["heading_1"] = text
                chunk_buffer["heading_2"] = None
                chunk_buffer["heading_3"] = None
                path = [text]
            elif heading_level == 2:
                chunk_buffer["heading_2"] = text
                chunk_buffer["heading_3"] = None
                path = path[:1] + [text] if path else [text]
            elif heading_level == 3:
                chunk_buffer["heading_3"] = text
                path = path[:2] + [text] if len(path) >= 2 else path + [text]
            
            chunk_buffer["section"] = " > ".join(path)
            
            # Add heading as first line of chunk
            if text:
                chunk_buffer["text"] = text + "\n"
                chunk_buffer["element_types"].append(f"heading_{heading_level}")
        
        # Handle paragraphs
        elif element_type == "p":
            if text:
                # Check if adding this text would exceed chunk size
                new_length = len(chunk_buffer["text"]) + len(text) + 1
                
                if new_length > StructuredChunker.MAX_CHUNK_SIZE and chunk_buffer["text"].strip():
                    # Flush and start new chunk
                    chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
                    chunk_buffer = {
                        "text": text + "\n",
                        "heading_1": chunk_buffer.get("heading_1"),
                        "heading_2": chunk_buffer.get("heading_2"),
                        "heading_3": chunk_buffer.get("heading_3"),
                        "page_number": chunk_buffer.get("page_number"),
                        "element_types": ["paragraph"],
                        "section": chunk_buffer.get("section"),
                    }
                else:
                    chunk_buffer["text"] += text + "\n"
                    if "paragraph" not in chunk_buffer["element_types"]:
                        chunk_buffer["element_types"].append("paragraph")
        
        # Handle tables
        elif element_type == "table":
            if chunk_buffer["text"].strip():
                chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
            
            # Extract table content
            table_text = StructuredChunker._extract_table_text(element)
            if table_text:
                chunk_buffer = {
                    "text": table_text,
                    "heading_1": chunk_buffer.get("heading_1"),
                    "heading_2": chunk_buffer.get("heading_2"),
                    "heading_3": chunk_buffer.get("heading_3"),
                    "page_number": chunk_buffer.get("page_number"),
                    "element_types": ["table"],
                    "section": chunk_buffer.get("section"),
                    "is_table": True,
                }
                chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
            
            chunk_buffer = {
                "text": "",
                "heading_1": chunk_buffer.get("heading_1"),
                "heading_2": chunk_buffer.get("heading_2"),
                "heading_3": chunk_buffer.get("heading_3"),
                "page_number": chunk_buffer.get("page_number"),
                "element_types": [],
                "section": chunk_buffer.get("section"),
            }
        
        # Handle lists
        elif element_type == "list":
            list_text = StructuredChunker._extract_list_text(element)
            if list_text:
                if chunk_buffer["text"].strip():
                    chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
                
                chunk_buffer = {
                    "text": list_text,
                    "heading_1": chunk_buffer.get("heading_1"),
                    "heading_2": chunk_buffer.get("heading_2"),
                    "heading_3": chunk_buffer.get("heading_3"),
                    "page_number": chunk_buffer.get("page_number"),
                    "element_types": ["list"],
                    "section": chunk_buffer.get("section"),
                }
                chunks.append(StructuredChunker._finalize_chunk(chunk_buffer, len(chunks) + 1))
                
                chunk_buffer = {
                    "text": "",
                    "heading_1": chunk_buffer.get("heading_1"),
                    "heading_2": chunk_buffer.get("heading_2"),
                    "heading_3": chunk_buffer.get("heading_3"),
                    "page_number": chunk_buffer.get("page_number"),
                    "element_types": [],
                    "section": chunk_buffer.get("section"),
                }
        
        return chunk_buffer
    
    @staticmethod
    def _extract_table_text(table_element: Dict[str, Any]) -> str:
        """
        Extract readable text from table element.
        
        Args:
            table_element: Table element from Adobe JSON
            
        Returns:
            Formatted table text
        """
        rows = []
        for row in table_element.get("elements", []):
            cells = []
            for cell in row.get("elements", []):
                # Adobe uses capital "Text" not "text"
                cell_text = cell.get("Text", "").strip()
                cells.append(cell_text)
            if cells:
                rows.append(" | ".join(cells))
        
        return "\n".join(rows) if rows else ""
    
    @staticmethod
    def _extract_list_text(list_element: Dict[str, Any]) -> str:
        """
        Extract readable text from list element.
        
        Args:
            list_element: List element from Adobe JSON
            
        Returns:
            Formatted list text
        """
        items = []
        for item in list_element.get("elements", []):
            # Adobe uses capital "Text" not "text"
            item_text = item.get("Text", "").strip()
            if item_text:
                items.append(f"• {item_text}")
        
        return "\n".join(items) if items else ""
    
    @staticmethod
    def _finalize_chunk(chunk_buffer: Dict[str, Any], chunk_index: int) -> Dict[str, Any]:
        """
        Finalize a chunk with metadata.
        
        Args:
            chunk_buffer: Buffer to finalize
            chunk_index: Index of chunk
            
        Returns:
            Completed chunk dictionary
        """
        text = chunk_buffer["text"].strip()
        
        return {
            "chunk_index": chunk_index,
            "page_number": chunk_buffer.get("page_number"),
            "section": chunk_buffer.get("section"),
            "text": text,
            "char_count": len(text),
            "word_count": len(text.split()),
            "heading_level_1": chunk_buffer.get("heading_1"),
            "heading_level_2": chunk_buffer.get("heading_2"),
            "heading_level_3": chunk_buffer.get("heading_3"),
            "element_type": chunk_buffer.get("element_types")[0] if chunk_buffer.get("element_types") else "paragraph",
            "extraction_source": "adobe_api",
            "confidence_score": 0.95,  # Adobe extractions are generally high confidence
            "is_table": chunk_buffer.get("is_table", False),
        }
