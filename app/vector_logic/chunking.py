import pdfplumber
from typing import List, Dict





# Function to chunk text into chunks
def chunk_by_pages(file_path: str, min_chunk_size: int = 50) -> List[Dict]:
    """
    Chunk document by pages - each page becomes a separate chunk.
    
    Args:
        file_path: Path to the PDF file
        min_chunk_size: Minimum character count for a chunk to be included
    
    Returns:
        List of chunk dictionaries with page information
    """
    chunks = []
    
    with pdfplumber.open(file_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text()
            
            if page_text and len(page_text.strip()) >= min_chunk_size:
                chunks.append({
                    "chunk_index": len(chunks) + 1,
                    "page_number": page_num,
                    "text": page_text.strip(),
                    "char_count": len(page_text.strip()),
                    "word_count": len(page_text.strip().split())
                })
    
    return chunks