import chromadb
from typing import List, Dict, Any
import os
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import threading
from functools import lru_cache

from app.core.config import settings

# Thread-local storage for ChromaDB clients (one per thread)
_thread_local = threading.local()


def _get_persist_directory(persist_directory: str | None = None) -> str:
    """
    Resolve the directory where ChromaDB data is stored.
    Defaults to app/vector_db/chroma_db.
    """
    if persist_directory is not None:
        return persist_directory
    
    if settings.chromadb_persist_directory:
        return settings.chromadb_persist_directory

    base_dir = Path(__file__).resolve().parents[1]  # Go up to app/ directory
    return str(base_dir / "vector_db" / "chroma_db")


# Global ChromaDB client singleton (thread-safe)
# We use a lock to ensure only one thread initializes the client at a time.
# This prevents race conditions where multiple clients might try to open the same SQLite DB.
_chroma_client_lock = threading.Lock()
_chroma_client_instance = None
_chroma_persist_path = None


def _get_chroma_client(persist_directory: str | None = None) -> chromadb.ClientAPI:
    """
    Get ChromaDB client with singleton pattern for thread-safe access.
    Uses thread-local storage to ensure each thread has its own client instance.
    """
    global _chroma_client_instance, _chroma_persist_path
    
    path = _get_persist_directory(persist_directory)
    
    # Use thread-local storage for thread safety
    if not hasattr(_thread_local, 'chroma_client') or _thread_local.persist_path != path:
        with _chroma_client_lock:
            # Create thread-local client
            # ChromaDB (via SQLite) can be sensitive to sharing connections across threads.
            # Using thread-local storage ensures each request thread gets its own isolated connection.
            _thread_local.chroma_client = chromadb.PersistentClient(
                path=path,
                settings=chromadb.Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                )
            )
            _thread_local.persist_path = path
    
    return _thread_local.chroma_client


@lru_cache(maxsize=1)
def get_chroma_client_cached(persist_directory: str | None = None) -> chromadb.ClientAPI:
    """
    Cached version of ChromaDB client getter (for single-process use).
    Use _get_chroma_client() for multiprocessing scenarios.
    """
    return _get_chroma_client(persist_directory)


def init_chromadb(persist_directory: str | None = None) -> bool:
    """
    Initialize ChromaDB connection and verify it works.
    Call this during app startup.
    """
    try:
        client = _get_chroma_client(persist_directory)
        # Test connection by listing collections
        _ = client.list_collections()
        print("[OK] ChromaDB connection initialized successfully")
        return True
    except Exception as e:
        print(f"[FAIL] ChromaDB connection failed: {e}")
        return False


def embed_worker(text: str) -> list:
    """
    Worker function to generate embeddings for a single text chunk.
    Used in a process pool for CPU-parallel embedding generation.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    return model.encode(text, show_progress_bar=False).tolist()


_embedding_model_instance = None

def _get_embedding_model():
    """Get or create cached SentenceTransformer model"""
    global _embedding_model_instance
    if _embedding_model_instance is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model_instance = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model_instance


def _embed_query(text: str) -> list:
    """
    Generate embeddings for a query string (synchronous, single process).
    """
    model = _get_embedding_model()
    # show_progress_bar=False prevents tqdm from touching sys.stderr,
    # which avoids OSError [Errno 22] on Windows when stderr has been
    # reconfigured or is captured by a process manager (e.g. uvicorn --reload).
    return model.encode(text, show_progress_bar=False).tolist()


def store_chunks_in_chromadb(
    chunks: List[Dict],
    collection_name: str = "pdf_pages",
    persist_directory: str | None = None,
) -> chromadb.Collection:
    """
    Store pre-chunked document text in a ChromaDB collection, with embeddings.
    """
    persist_directory = _get_persist_directory(persist_directory)

    # -----------------------------
    # 1. Prepare documents
    # -----------------------------
    documents = [chunk["text"] for chunk in chunks]

    # -----------------------------
    # 2. CPU-parallel embeddings (optimized for multiprocessing)
    # -----------------------------
    num_workers = settings.max_workers

    print(f"Generating embeddings using {num_workers} CPU processes...")

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        # MAP: Distribute the list of text chunks across multiple CPU cores.
        # This allows us to calculate embeddings (math heavy) in parallel, speeding up uploads by 4-8x.
        embeddings = list(executor.map(embed_worker, documents))

    # -----------------------------
    # 3. Init ChromaDB
    # -----------------------------
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
        print(f"Using existing collection: {collection_name}")
    except Exception:
        collection = client.create_collection(
            name=collection_name,
            metadata={"description": f"Chunks for collection {collection_name}"},
        )
        print(f"Created new collection: {collection_name}")

    # -----------------------------
    # 4. Prepare metadata & IDs
    # -----------------------------
    ids: List[str] = []
    metadatas: List[Dict[str, Any]] = []

    for idx, chunk in enumerate(chunks, start=1):
        # Use document-scoped IDs to avoid collisions when multiple docs share a collection.
        # ChromaDB overwrites on duplicate IDs, so chunk_1 from doc A was overwritten by doc B.
        doc_id = chunk.get("document_id")
        chunk_id = f"doc_{doc_id}_chunk_{idx}" if doc_id is not None else f"chunk_{idx}"
        ids.append(chunk_id)

        metadata: Dict[str, Any] = {
            "page_number": chunk.get("page_number"),
            "chunk_index": chunk.get("chunk_index"),
            "char_count": chunk.get("char_count"),
            "word_count": chunk.get("word_count"),
            "source": "page_chunker",
        }

        # Optional: allow passing document-related metadata
        if "document_id" in chunk:
            metadata["document_id"] = chunk["document_id"]
        if "version" in chunk:
            metadata["version"] = chunk["version"]

        metadatas.append(metadata)

    # -----------------------------
    # 5. Single DB write (SAFE)
    # -----------------------------
    print("Storing chunks in ChromaDB...")
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    print(f"Stored {len(chunks)} chunks in '{collection_name}'")
    print(f"DB path: {os.path.abspath(persist_directory)}")

    return collection


def list_collections(persist_directory: str | None = None) -> List[Dict[str, Any]]:
    """
    List available ChromaDB collections with basic metadata.
    """
    client = _get_chroma_client(persist_directory)
    collections = client.list_collections()
    result: List[Dict[str, Any]] = []

    for col in collections:
        result.append(
            {
                "name": col.name,
                "metadata": getattr(col, "metadata", {}) or {},
            }
        )

    return result


def query_collection(
    query_text: str,
    collection_name: str,
    n_results: int = 5,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    """
    Query a ChromaDB collection using a natural language query.

    Returns a dictionary with ids, documents, metadatas and distances.
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        raise ValueError(f"Collection '{collection_name}' does not exist")

    # Embed the query using the same model used for documents
    query_embedding = _embed_query(query_text)

    # Note: Chroma always returns IDs; `include` controls extra fields
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    return results

def query_collection_with_filter(
    query_text: str,
    collection_name: str,
    n_results: int = 5,
    where: Dict[str, Any] | None = None,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    """
    Query a ChromaDB collection with optional metadata filtering.
    
    Uses 'where' clause to filter chunks at query time instead of 
    post-retrieval filtering. This is more efficient and accurate.
    
    Args:
        query_text: Natural language query
        collection_name: Name of the collection to search
        n_results: Number of results to return
        where: Optional filter dict, e.g., {"document_id": 123}
        persist_directory: Path to ChromaDB storage
    
    Returns:
        Dictionary with ids, documents, metadatas and distances.
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)

    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        raise ValueError(f"Collection '{collection_name}' does not exist")

    # Embed the query using the same model used for documents
    query_embedding = _embed_query(query_text)

    # Query with optional where filter
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
        where=where  # ChromaDB native filtering
    )

    return results


def store_document_in_master_collection(
    document_id: int,
    title: str,
    description: str,
    category: str | None = None,
    source_type: str = "pdf",
    persist_directory: str | None = None,
    doc_type: str | None = None,
) -> None:
    """
    Store document metadata in the master_docs collection.
    
    ARCHITECTURE NOTE:
    We use a "Two-Layer Search" strategy:
    1. Layer 1 (Master Collection): Stores ONLY metadata (Title, Description, Category). 
       We search this first to find *relevant documents*.
    2. Layer 2 (Category Collections): Stores the actual *content chunks*.
       We search the documents found in Layer 1 to find *specific answers*.
    
    This function handles Layer 1.
    
    Args:
        document_id: Document ID from SQLite
        title: Document title
        description: Document description (AI-generated)
        category: Document category
        source_type: Type of document (pdf, etc.)
        persist_directory: Path to ChromaDB storage
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    # Create master_docs collection if it doesn't exist
    try:
        master_collection = client.get_collection(name="master_docs")
    except Exception:
        master_collection = client.create_collection(
            name="master_docs",
            metadata={"description": "Master collection for document-level search"}
        )
        print("Created master_docs collection")
    
    # Build document text for embedding (title + description + metadata)
    doc_text_parts = [f"Title: {title}"]
    
    if description:
        doc_text_parts.append(f"Description: {description}")
    
    if category:
        doc_text_parts.append(f"Category: {category}")
    
    # Logical content type (proposal / case_study / solution / other)
    if doc_type:
        doc_text_parts.append(f"DocType: {doc_type}")
    
    # Physical source type (pdf / docx / txt, etc.)
    doc_text_parts.append(f"Type: {source_type}")
    
    doc_text = "\n".join(doc_text_parts)
    
    # Generate embedding for the document
    doc_embedding = _embed_query(doc_text)
    
    # Prepare metadata
    metadata = {
        "document_id": document_id,
        "title": title,
        "category": category or "uncategorized",
        "source_type": source_type,
        "doc_type": doc_type or "other",
    }
    
    # Upsert (Update or Insert)
    # If the document_id already exists, this overwrites it. 
    # This acts as an "Auto-Update" if you re-upload the same file.
    master_collection.upsert(
        ids=[str(document_id)],
        documents=[doc_text],        # Ssearchable text (Title + Description)
        embeddings=[doc_embedding],  # Vector representation of the text
        metadatas=[metadata]         # Filtering fields (Category, Type)
    )
    
    print(f"Stored document {document_id} in master_docs collection")


def delete_document_from_chromadb(
    document_id: int,
    collection_name: str | None = None,
    persist_directory: str | None = None,
) -> None:
    """
    Delete a document from ChromaDB collections.
    Removes chunks from category-based collection and document from master_docs collection.
    
    Args:
        document_id: Document ID to delete
        collection_name: Category-based collection name (optional, will try to find if not provided)
        persist_directory: Path to ChromaDB storage
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    deleted_from_collections = []
    
    # 1. Delete from master_docs collection
    try:
        master_collection = client.get_collection(name="master_docs")
        # Delete by ID (document_id is stored as string ID)
        master_collection.delete(ids=[str(document_id)])
        deleted_from_collections.append("master_docs")
        print(f"Deleted document {document_id} from master_docs collection")
    except Exception as e:
        print(f"Warning: Could not delete from master_docs collection: {e}")
    
    # 2. Delete chunks from category-based collection
    if collection_name:
        try:
            collection = client.get_collection(name=collection_name)
            # Delete chunks where metadata contains this document_id
            # Delete chunks where metadata contains this document_id
            # ChromaDB delete supports where clause for metadata filtering.
            # This effectively performs a "Cascade Delete" of all chunks belonging to this file.
            collection.delete(where={"document_id": document_id})
            deleted_from_collections.append(collection_name)
            print(f"Deleted chunks from {collection_name} collection for document {document_id}")
        except Exception as e:
            print(f"Warning: Could not delete from {collection_name} collection: {e}")
    else:
        # If collection_name not provided, try to find and delete from all collections
        try:
            collections = client.list_collections()
            for col in collections:
                if col.name == "master_docs":
                    continue  # Already handled
                try:
                    collection = client.get_collection(name=col.name)
                    # Delete chunks with this document_id using where clause
                    collection.delete(where={"document_id": document_id})
                    deleted_from_collections.append(col.name)
                    print(f"Deleted chunks from {col.name} collection for document {document_id}")
                except Exception as e:
                    # If delete fails, it might be because no chunks exist for this document_id
                    # This is not necessarily an error, so we just log it
                    print(f"Info: No chunks found in {col.name} collection for document {document_id}: {e}")
        except Exception as e:
            print(f"Warning: Could not list collections: {e}")
    
    if deleted_from_collections:
        print(f"Successfully deleted document {document_id} from ChromaDB collections: {', '.join(deleted_from_collections)}")
    else:
        print(f"Warning: Document {document_id} was not found in any ChromaDB collections")


def query_master_collection(
    query_text: str,
    n_results: int = 5,
    persist_directory: str | None = None,
) -> Dict[str, Any]:
    """
    Query the master_docs collection to find relevant documents.
    
    Returns a dictionary with ids, documents, metadatas and distances.
    """
    return query_collection(
        query_text=query_text,
        collection_name="master_docs",
        n_results=n_results,
        persist_directory=persist_directory
    )


def rename_chromadb_collection(
    old_collection_name: str,
    new_collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    """
    Rename a ChromaDB collection by copying data to a new collection and deleting the old one.
    Preserves or updates collection metadata with category description.
    
    Args:
        old_collection_name: Current collection name
        new_collection_name: New collection name
        category_description: Optional category description to store in collection metadata
        persist_directory: Path to ChromaDB storage
    
    Returns:
        True if successful, False otherwise
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        # Check if old collection exists
        try:
            old_collection = client.get_collection(name=old_collection_name)
        except Exception:
            print(f"Collection '{old_collection_name}' does not exist, nothing to rename")
            return True  # Not an error if collection doesn't exist
        
        # Check if new collection already exists
        try:
            existing_collection = client.get_collection(name=new_collection_name)
            raise ValueError(f"Collection '{new_collection_name}' already exists")
        except Exception:
            # Collection doesn't exist, which is what we want
            pass
        
        # Get all data from old collection
        all_data = old_collection.get()
        
        if not all_data or not all_data.get("ids") or len(all_data["ids"]) == 0:
            # Empty collection, just delete the old one and create new empty one
            # Try to preserve old collection metadata
            metadata = {}
            if category_description:
                metadata["description"] = category_description
            else:
                try:
                    old_metadata = old_collection.metadata or {}
                    old_desc = old_metadata.get("description")
                    if old_desc:
                        metadata["description"] = old_desc
                    else:
                        metadata["description"] = f"Renamed from {old_collection_name}"
                except:
                    metadata["description"] = f"Renamed from {old_collection_name}"
            
            client.delete_collection(name=old_collection_name)
            client.create_collection(
                name=new_collection_name,
                metadata=metadata
            )
            print(f"Renamed empty collection '{old_collection_name}' to '{new_collection_name}'")
            return True
        
        # Create new collection with description if provided
        metadata = {}
        if category_description:
            metadata["description"] = category_description
        else:
            # Try to preserve old collection metadata if available
            try:
                old_metadata = old_collection.metadata or {}
                old_desc = old_metadata.get("description")
                if old_desc:
                    metadata["description"] = old_desc
                else:
                    metadata["description"] = f"Renamed from {old_collection_name}"
            except:
                metadata["description"] = f"Renamed from {old_collection_name}"
        
        new_collection = client.create_collection(
            name=new_collection_name,
            metadata=metadata
        )
        
        # Copy all data to new collection
        ids = all_data.get("ids", [])
        documents = all_data.get("documents", [])
        metadatas = all_data.get("metadatas", [])
        embeddings = all_data.get("embeddings", [])
        
        # Add all data to new collection
        new_collection.add(
            ids=ids,
            documents=documents if documents else None,
            metadatas=metadatas if metadatas else None,
            embeddings=embeddings if embeddings else None
        )
        
        # Delete old collection
        client.delete_collection(name=old_collection_name)
        
        print(f"Successfully renamed collection '{old_collection_name}' to '{new_collection_name}' ({len(ids)} items)")
        return True
        
    except Exception as e:
        print(f"Error renaming collection '{old_collection_name}' to '{new_collection_name}': {e}")
        return False


def ensure_collection_exists(
    collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    """
    Ensure a ChromaDB collection exists, create it if it doesn't.
    Updates collection metadata with category description if provided.
    
    Args:
        collection_name: Name of the collection
        category_description: Optional description from category (stored in collection metadata)
        persist_directory: Path to ChromaDB storage
    
    Returns:
        True if collection exists or was created, False otherwise
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        # Try to get the collection
        collection = client.get_collection(name=collection_name)
        
        # Update metadata if description is provided and different
        if category_description is not None:
            current_metadata = collection.metadata or {}
            current_desc = current_metadata.get("description", "")
            # Only update if description changed
            if current_desc != category_description:
                # ChromaDB doesn't support direct metadata update, so we need to recreate
                # For now, we'll just log it - full update would require recreating collection
                print(f"Note: Collection '{collection_name}' metadata update would require collection recreation")
                # The description is already in SQLite, so this is mainly for reference
        return True
    except Exception:
        # Collection doesn't exist, create it
        try:
            metadata = {}
            if category_description:
                metadata["description"] = category_description
            else:
                metadata["description"] = f"Collection for category: {collection_name}"
            
            client.create_collection(
                name=collection_name,
                metadata=metadata
            )
            print(f"Created ChromaDB collection: {collection_name} with description: {category_description or 'N/A'}")
            return True
        except Exception as e:
            print(f"Error creating collection '{collection_name}': {e}")
            return False


def update_collection_metadata(
    collection_name: str,
    category_description: str | None = None,
    persist_directory: str | None = None,
) -> bool:
    """
    Update ChromaDB collection metadata by recreating the collection.
    Since ChromaDB doesn't support direct metadata updates, we recreate the collection.
    
    Args:
        collection_name: Name of the collection to update
        category_description: New description to store in collection metadata
        persist_directory: Path to ChromaDB storage
    
    Returns:
        True if successful, False otherwise
    """
    persist_directory = _get_persist_directory(persist_directory)
    client = _get_chroma_client(persist_directory)
    
    try:
        # Check if collection exists
        try:
            collection = client.get_collection(name=collection_name)
        except Exception:
            print(f"Collection '{collection_name}' does not exist, nothing to update")
            return True  # Not an error if collection doesn't exist
        
        # Get all data from existing collection
        all_data = collection.get()
        
        # Get current metadata
        current_metadata = collection.metadata or {}
        current_desc = current_metadata.get("description", "")
        
        # Check if description actually changed
        if category_description is not None and current_desc == category_description:
            print(f"Collection '{collection_name}' metadata already has the same description, no update needed")
            return True
        
        # Prepare new metadata
        metadata = {}
        if category_description:
            metadata["description"] = category_description
        else:
            # Keep existing description if no new one provided
            if current_desc:
                metadata["description"] = current_desc
            else:
                metadata["description"] = f"Collection for category: {collection_name}"
        
        # If collection is empty, just delete and recreate
        if not all_data or not all_data.get("ids") or len(all_data["ids"]) == 0:
            client.delete_collection(name=collection_name)
            client.create_collection(
                name=collection_name,
                metadata=metadata
            )
            print(f"Updated metadata for empty collection '{collection_name}'")
            return True
        
        # Collection has data - need to recreate it
        # Get all data
        ids = all_data.get("ids", [])
        documents = all_data.get("documents", [])
        metadatas = all_data.get("metadatas", [])
        embeddings = all_data.get("embeddings", [])
        
        # Create temporary collection name
        temp_collection_name = f"{collection_name}_temp_{os.getpid()}"
        
        # Create new collection with updated metadata
        new_collection = client.create_collection(
            name=temp_collection_name,
            metadata=metadata
        )
        
        # Copy all data to new collection
        new_collection.add(
            ids=ids,
            documents=documents if documents else None,
            metadatas=metadatas if metadatas else None,
            embeddings=embeddings if embeddings else None
        )
        
        # Delete old collection
        client.delete_collection(name=collection_name)
        
        # Rename temp collection to original name (by recreating with original name)
        # Get data from temp collection
        temp_data = new_collection.get()
        temp_ids = temp_data.get("ids", [])
        temp_documents = temp_data.get("documents", [])
        temp_metadatas = temp_data.get("metadatas", [])
        temp_embeddings = temp_data.get("embeddings", [])
        
        # Delete temp collection
        client.delete_collection(name=temp_collection_name)
        
        # Create collection with original name and updated metadata
        final_collection = client.create_collection(
            name=collection_name,
            metadata=metadata
        )
        
        # Add all data back
        final_collection.add(
            ids=temp_ids,
            documents=temp_documents if temp_documents else None,
            metadatas=temp_metadatas if temp_metadatas else None,
            embeddings=temp_embeddings if temp_embeddings else None
        )
        
        print(f"Successfully updated metadata for collection '{collection_name}' ({len(temp_ids)} items)")
        return True
        
    except Exception as e:
        print(f"Error updating collection metadata for '{collection_name}': {e}")
        import traceback
        traceback.print_exc()
        return False
