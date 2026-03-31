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
    raise RuntimeError(
        "pdfplumber-based chunking is deprecated. "
        "The system uses the OCR pipeline as the only extraction source."
    )