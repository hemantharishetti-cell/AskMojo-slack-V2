#!/usr/bin/env python3
"""
Simple script to view extracted PDF content and chunks in logs.

USAGE:
    python view_pdf_extraction.py <document_id>

EXAMPLES:
    python view_pdf_extraction.py 42
    python view_pdf_extraction.py 1

This will show:
    • All extracted elements (text, type, page)
    • All chunks created from those elements
    • Statistics and summary
"""

import sys
import json
from pathlib import Path


def view_extraction(document_id: int) -> None:
    """View extracted content for a document."""
    
    # Find the extraction analysis directory
    analysis_dir = Path(__file__).parent / "app" / "extraction_analysis"
    
    if not analysis_dir.exists():
        print(f"❌ No extraction analysis directory found at: {analysis_dir}")
        print("   Run a PDF extraction first to generate results.")
        return
    
    # Find files for this document (get the most recent)
    extraction_files = sorted(
        analysis_dir.glob(f"doc_{document_id}_*_extraction.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    
    chunks_files = sorted(
        analysis_dir.glob(f"doc_{document_id}_*_chunks.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )
    
    if not extraction_files or not chunks_files:
        print(f"❌ No extraction results found for document ID: {document_id}")
        print(f"   Looked in: {analysis_dir}")
        print(f"\n   Available documents:")
        doc_dirs = set()
        for f in analysis_dir.glob("doc_*_extraction.json"):
            doc_id = f.name.split("_")[1]
            doc_dirs.add(doc_id)
        for doc_id in sorted(doc_dirs):
            print(f"     • Document {doc_id}")
        return
    
    # Load extraction and chunks
    extraction_file = extraction_files[0]
    chunks_file = chunks_files[0]
    
    with open(extraction_file) as f:
        extracted = json.load(f)
    
    with open(chunks_file) as f:
        chunks = json.load(f)
    
    elements = extracted.get("elements", [])
    
    # Print the output
    print("\n" + "="*100)
    print(f"📄 PDF EXTRACTION RESULTS - Document ID: {document_id}")
    print("="*100)
    
    # Extraction stats
    print(f"\n🔍 EXTRACTION SUMMARY")
    print(f"  ├─ Total Elements Extracted: {len(elements)}")
    
    # Count element types
    element_types = {}
    for elem in elements:
        path = elem.get("Path", "").lower()
        if "/h1" in path:
            t = "H1 (Heading 1)"
        elif "/h2" in path:
            t = "H2 (Heading 2)"
        elif "/h3" in path:
            t = "H3 (Heading 3)"
        elif "/p" in path:
            t = "P (Paragraph)"
        elif "/table" in path:
            t = "TABLE"
        elif "/list" in path or "/li" in path:
            t = "LIST"
        else:
            t = "OTHER"
        element_types[t] = element_types.get(t, 0) + 1
    
    print(f"  ├─ Element Type Distribution:")
    for elem_type, count in sorted(element_types.items()):
        print(f"  │  ├─ {elem_type}: {count}")
    
    # Show all extracted elements
    print(f"\n📋 ALL EXTRACTED ELEMENTS ({len(elements)} total)")
    print("─" * 100)
    for idx, elem in enumerate(elements, 1):
        path = elem.get("Path", "???")
        text = elem.get("Text", "")
        page = elem.get("Page", "?")
        
        # Truncate long text
        if len(text) > 80:
            display_text = text[:77] + "..."
        else:
            display_text = text
        
        print(f"  [{idx:3d}] Path: {path}")
        print(f"         Page: {page}")
        print(f"         Text: {display_text}")
        if idx < len(elements):
            print()
    
    # Chunking stats
    print(f"\n{'='*100}")
    print(f"✂️  CHUNKING SUMMARY")
    print(f"  ├─ Total Chunks Created: {len(chunks)}")
    
    if chunks:
        total_chars = sum(c.get("char_count", 0) for c in chunks)
        total_words = sum(c.get("word_count", 0) for c in chunks)
        avg_chars = total_chars / len(chunks) if chunks else 0
        print(f"  ├─ Total Characters: {total_chars:,}")
        print(f"  ├─ Total Words: {total_words:,}")
        print(f"  └─ Average Chunk Size: {avg_chars:.0f} chars")
    
    # Show all chunks
    print(f"\n📑 ALL CHUNKS CREATED ({len(chunks)} total)")
    print("─" * 100)
    for idx, chunk in enumerate(chunks, 1):
        section = chunk.get("section", "No section")
        page = chunk.get("page_number", "?")
        text = chunk.get("text", "")
        char_count = chunk.get("char_count", 0)
        
        # Truncate long text
        if len(text) > 80:
            display_text = text[:77] + "..."
        else:
            display_text = text
        
        element_types_in_chunk = ", ".join(chunk.get("element_types", []))
        
        print(f"  [CHUNK {idx}]")
        print(f"    ├─ Section: {section}")
        print(f"    ├─ Page: {page}")
        print(f"    ├─ Types: {element_types_in_chunk}")
        print(f"    ├─ Size: {char_count} chars")
        print(f"    └─ Text: {display_text}")
        if idx < len(chunks):
            print()
    
    print("\n" + "="*100)
    print(f"✅ Complete extraction and chunking data saved to: {analysis_dir}")
    print("="*100 + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python view_pdf_extraction.py <document_id>")
        print("\nExamples:")
        print("  python view_pdf_extraction.py 42")
        print("  python view_pdf_extraction.py 1")
        sys.exit(1)
    
    try:
        doc_id = int(sys.argv[1])
        view_extraction(doc_id)
    except ValueError:
        print(f"❌ Error: '{sys.argv[1]}' is not a valid document ID (must be a number)")
        sys.exit(1)
