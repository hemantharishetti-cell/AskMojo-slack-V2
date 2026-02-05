import asyncio
from pathlib import Path

from app.sqlite.models import Document, DocumentVersion, DocumentChunk, DocumentUploadLog
from datetime import datetime
from app.vector_logic.chunking import chunk_by_pages
from app.vector_logic.vector_store import (
    store_chunks_in_chromadb,
    store_document_in_master_collection
)
from app.core.config import settings
from app.sqlite.database import SessionLocal


async def process_document_async(
    document_id: int,
    delay_seconds: int = 5,
    collection_name: str = "pdf_pages",
    persist_directory: str = None
):
    """
    Background task to process a document after a delay.
    
    Args:
        document_id: ID of the document to process
        delay_seconds: Delay before processing starts
        collection_name: ChromaDB collection name
        persist_directory: Path to ChromaDB storage (defaults to ../vector_db/chroma_db)
    """
    # Wait for the specified delay
    await asyncio.sleep(delay_seconds)
    
    # Create a new database session for this background task
    db = SessionLocal()
    
    try:
        # Get the document
        document = db.query(Document).filter(Document.id == document_id).first()
        
        if not document:
            print(f"Document {document_id} not found")
            return
        
        if not document.file_path:
            print(f"Document {document_id} has no file_path")
            return
        
        if document.processed:
            print(f"Document {document_id} already processed")
            return
        
        # Update upload log - mark processing as started
        upload_log = db.query(DocumentUploadLog).filter(
            DocumentUploadLog.document_id == document_id
        ).first()
        if upload_log:
            upload_log.processing_started = True
            db.commit()
        
        print(f"Starting vector processing for document {document_id}: {document.title}")

        # Use category as collection name if not provided, or derive from document
        if not collection_name or collection_name == "documents":
            if document.category:
                collection_name = document.category.lower().replace(" ", "_")
            else:
                collection_name = "documents"

        print(f"Using collection: {collection_name}")

        # Check if file exists
        file_path = Path(document.file_path)
        if not file_path.exists():
            print(f"File not found: {file_path}")
            db.close()
            return

        # 1. Chunk the document
        print(f"Chunking document: {file_path}")
        chunks = chunk_by_pages(str(file_path))

        if not chunks:
            print(f"No chunks extracted from document {document_id}")
            db.close()
            return

        print(f"Extracted {len(chunks)} chunks")

        # Attach document metadata to chunks for vector store
        for chunk in chunks:
            chunk["document_id"] = document.id
            chunk["version"] = 1  # For now we always use version 1; can be extended

        # 2. Store in ChromaDB (category-based collection)
        if persist_directory is None:
            base_dir = Path(__file__).resolve().parents[1]  # Go up to app/ directory
            persist_directory = str(base_dir / "vector_db" / "chroma_db")

        print(f"Storing chunks in ChromaDB collection: {collection_name}")
        collection = store_chunks_in_chromadb(
            chunks=chunks,
            collection_name=collection_name,
            persist_directory=persist_directory,
        )
        
        # 3. Create DocumentVersion record
        document_version = DocumentVersion(
            document_id=document.id,
            version=1,  # First version
            file_path=str(file_path),
            checksum=None  # Can add checksum calculation later
        )
        db.add(document_version)
        db.commit()
        db.refresh(document_version)
        
        # 4. Create DocumentChunk records
        for chunk in chunks:
            document_chunk = DocumentChunk(
                document_id=document.id,
                version_id=document_version.id,
                version=document_version.version,  # Use version number from DocumentVersion
                chunk_index=chunk["chunk_index"],
                page_number=chunk["page_number"],
                section=None  # Can be extracted later if needed
            )
            db.add(document_chunk)
        
        # 5. Store document in master_docs collection for document-level search
        print(f"Storing document metadata in master_docs collection...")
        store_document_in_master_collection(
            document_id=document.id,
            title=document.title,
            description=document.description or "",
            category=document.category,
            source_type=document.source_type,
            persist_directory=persist_directory
        )
        
        # 6. Mark document as processed
        document.processed = True
        db.commit()
        
        # Update upload log - mark processing as completed
        if upload_log:
            upload_log.processing_completed = True
            upload_log.processed_at = datetime.utcnow()
            db.commit()
        
        print(f"Successfully processed document {document_id}")
        
    except Exception as e:
        print(f"Error processing document {document_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        # Update upload log with error
        try:
            upload_log = db.query(DocumentUploadLog).filter(
                DocumentUploadLog.document_id == document_id
            ).first()
            if upload_log:
                upload_log.processing_error = str(e)[:500]  # Limit error message length
                upload_log.processing_started = True  # Mark as started even if failed
                db.commit()
        except:
            pass
    
    finally:
        db.close()


def process_document_background(
    document_id: int,
    delay_seconds: int = 5,
    collection_name: str = "pdf_pages",
    persist_directory: str = None
):
    """
    Synchronous wrapper to run the async processing task.
    This can be used with FastAPI BackgroundTasks.
    """
    # Create a new event loop for this background task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(
            process_document_async(
                document_id=document_id,
                delay_seconds=delay_seconds,
                collection_name=collection_name,
                persist_directory=persist_directory
            )
        )
    finally:
        loop.close()

