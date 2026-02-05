from fastapi import APIRouter, File, Form, UploadFile, Depends, BackgroundTasks, status
from sqlalchemy.orm import Session, joinedload
from app.sqlite.database import get_db
from app.sqlite.models import Document, Category, User, DocumentUploadLog, QueryLog
from app.core.security import get_current_admin_user
from openai import OpenAI
from app.vector_logic.processor import process_document_background
from toon import encode
import tiktoken
import re
from datetime import datetime

from app.vector_logic.schemas import (
    DocumentResponse,
    DocumentUpdate,
    DocumentStatusResponse,
    VectorQueryRequest,
    VectorQueryResult,
    AskRequest,
    AskResponse,
    SourceChunk,
    AIDecisionResponse,
)
from app.vector_logic.description_generator import generate_description, refine_description
from app.vector_logic.vector_store import (
    list_collections,
    query_collection,
    query_master_collection,
)
import json
import re
from app.core.config import settings
from fastapi import HTTPException
from uuid import uuid4
from os.path import splitext
import os
import shutil

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
UPLOAD_DIR = BASE_DIR / "app/uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

router = APIRouter(tags=["Documents"])


@router.post("/upload")
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    category_id: int | None = Form(None),  # Category ID from Category table
    category: str | None = Form(None),  # Legacy: kept for backward compatibility
    description: str | None = Form(None),  # Optional description (if provided, will be used; otherwise AI-generated from full PDF)
    internal_only: bool = Form(False),  # Whether document is internal-only
    current_user: User = Depends(get_current_admin_user),  # Admin authentication required
    db: Session = Depends(get_db),
):
    """
    Upload a document (admin only).
    Supports category_id (from Category table) or legacy category string.
    """
    # -----------------------------
    # 1. Validate file
    # -----------------------------
    if not file.filename:
        raise HTTPException(status_code=400, detail="Empty filename")

    # Validate file extension
    allowed_extensions = {'.pdf', '.doc', '.docx', '.txt'}
    ext = splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
        )

    # -----------------------------
    # 2. Validate category_id if provided
    # -----------------------------
    category_obj = None
    collection_name = "documents"  # Default collection name
    category_name = category  # Legacy category name
    
    if category_id:
        category_obj = db.query(Category).filter(Category.id == category_id).first()
        if not category_obj:
            raise HTTPException(
                status_code=404,
                detail=f"Category with id {category_id} not found"
            )
        if not category_obj.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot upload to inactive category: {category_obj.name}"
            )
        collection_name = category_obj.collection_name
        category_name = category_obj.name  # Use category name from database

    # -----------------------------
    # 3. Safe filename
    # -----------------------------
    safe_filename = f"{uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / safe_filename

    # -----------------------------
    # 4. Save file to disk
    # -----------------------------
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # -----------------------------
    # 5. Generate description (always generate, unless explicitly provided)
    # Track time and token usage
    # -----------------------------
    import time
    upload_start_time = time.time()
    description_generation_time = None
    description_tokens_info = None
    
    if not description:
        print(f"Generating description from full PDF for document: {title}")
        desc_start_time = time.time()
        description, description_tokens_info = generate_description(
        title=title,
            category=category_name,
        file_path=str(file_path),
        openai_api_key=settings.openai_api_key
    )
        desc_end_time = time.time()
        description_generation_time = desc_end_time - desc_start_time
        print(f"Generated description: {description[:100]}...")
        if description_tokens_info:
            print(f"Description generation used {description_tokens_info.get('total_tokens', 0)} tokens in {description_generation_time:.2f}s")
    else:
        print(f"Using provided description for document: {title}")

    # -----------------------------
    # 6. Detect source type from extension
    # -----------------------------
    source_type_map = {
        '.pdf': 'pdf',
        '.doc': 'doc',
        '.docx': 'docx',
        '.txt': 'txt'
    }
    source_type = source_type_map.get(ext, 'pdf')

    # -----------------------------
    # 7. Create DB record
    # -----------------------------
    document = Document(
        title=title,
        category=category_name,  # Legacy field
        category_id=category_id,  # New field
        description=description,
        source_type=source_type,
        file_name=file.filename,  # original name
        file_path=str(file_path),  # stored path
        internal_only=internal_only,
        processed=False,
        uploaded_by=current_user.id,  # Use authenticated admin user's ID
    )

    db.add(document)
    db.commit()
    db.refresh(document)

    # -----------------------------
    # 7.5. Create upload log entry with time and token tracking
    # -----------------------------
    upload_end_time = time.time()
    upload_total_time = upload_end_time - upload_start_time
    
    upload_log = DocumentUploadLog(
        document_id=document.id,
        uploaded_by=current_user.id,
        title=title,
        file_name=file.filename,
        category_id=category_id,
        category=category_name,
        description_generated=(description is None or description == ""),
        description_length=len(description) if description else None,
        processing_started=False,
        processing_completed=False,
        upload_time_seconds=upload_total_time,
        description_generation_time_seconds=description_generation_time,
        description_tokens_used=description_tokens_info.get('total_tokens') if description_tokens_info else None,
        description_tokens_prompt=description_tokens_info.get('prompt_tokens') if description_tokens_info else None,
        description_tokens_completion=description_tokens_info.get('completion_tokens') if description_tokens_info else None
    )
    db.add(upload_log)
    db.commit()

    # -----------------------------
    # 8. Schedule background processing with delay
    # Use collection_name from category if available, otherwise default
    # -----------------------------
    background_tasks.add_task(
        process_document_background,
        document_id=document.id,
        delay_seconds=settings.vector_processing_delay,
        collection_name=collection_name,  # Category-based collection
        persist_directory=None  # Will use default
    )

    return {
        "message": "Document uploaded successfully. Vector processing will start after delay.",
        "document_id": document.id,
        "title": document.title,
        "category": category_name,
        "category_id": category_id,
        "file_name": document.file_name,
        "processed": document.processed,
        "internal_only": document.internal_only,
        "processing_delay_seconds": settings.vector_processing_delay,
    }


@router.get("/documents", response_model=list[DocumentResponse])
def list_documents(
    skip: int = 0,
    limit: int = 100,
    category: str | None = None,
    processed: bool | None = None,
    db: Session = Depends(get_db),
):
    """
    Get all documents with optional filtering and pagination.
    Includes uploader information for display.
    """
    query = db.query(Document).options(joinedload(Document.uploader))
    
    # Apply filters
    if category:
        query = query.filter(Document.category == category)
    if processed is not None:
        query = query.filter(Document.processed == processed)
    
    # Apply pagination
    documents = query.order_by(Document.created_at.desc()).offset(skip).limit(limit).all()
    return documents


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: int, db: Session = Depends(get_db)):
    """
    Get a specific document by ID.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    return document


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
def get_document_status(document_id: int, db: Session = Depends(get_db)):
    """
    Get the processing status of a document.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    return document


@router.put("/{document_id}", response_model=DocumentResponse)
def update_document(
    document_id: int,
    document_update: DocumentUpdate,
    db: Session = Depends(get_db),
):
    """
    Update a document by ID.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Update only provided fields
    update_data = document_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(document, field, value)
    
    db.commit()
    db.refresh(document)
    return document


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_admin_user),  # Admin authentication required
    db: Session = Depends(get_db),
):
    """
    Delete a document by ID (admin only).
    This will delete:
    1. The document from SQLite database (cascade will handle related records)
    2. All chunks from ChromaDB category-based collection
    3. Document metadata from ChromaDB master_docs collection
    4. The physical file from disk
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Get collection name from category if available
    collection_name = None
    if document.category_id:
        # Get category to find collection name
        from app.sqlite.models import Category
        category = db.query(Category).filter(Category.id == document.category_id).first()
        if category:
            collection_name = category.collection_name
    elif document.category:
        # Fallback to legacy category string
        collection_name = document.category.lower().replace(" ", "_")
    
    # Delete from ChromaDB first (before deleting from SQLite)
    try:
        from app.vector_logic.vector_store import delete_document_from_chromadb
        delete_document_from_chromadb(
            document_id=document_id,
            collection_name=collection_name,
            persist_directory=None  # Use default
        )
    except Exception as e:
        print(f"Error deleting from ChromaDB: {e}")
        # Continue with SQLite deletion even if ChromaDB deletion fails
        # This ensures the document is removed from the database
    
    # Delete the physical file if it exists
    if document.file_path:
        file_path = Path(document.file_path)
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"Deleted file: {file_path}")
            except Exception as e:
                print(f"Error deleting file {file_path}: {str(e)}")
                # Continue with DB deletion even if file deletion fails
    
    # Delete related records that have foreign keys to this document
    # 1. Delete DocumentUploadLog records
    from app.sqlite.models import DocumentUploadLog
    upload_logs = db.query(DocumentUploadLog).filter(
        DocumentUploadLog.document_id == document_id
    ).all()
    for log in upload_logs:
        db.delete(log)
    print(f"Deleted {len(upload_logs)} upload log(s) for document {document_id}")
    
    # 2. Delete QuerySource records (these reference documents via document_id)
    from app.sqlite.models import QuerySource
    query_sources = db.query(QuerySource).filter(
        QuerySource.document_id == document_id
    ).all()
    for source in query_sources:
        db.delete(source)
    print(f"Deleted {len(query_sources)} query source(s) for document {document_id}")
    
    # Commit the deletions of related records first
    db.commit()
    
    # Now delete from database (cascade will handle related records: DocumentVersion, DocumentChunk, etc.)
    db.delete(document)
    db.commit()
    
    print(f"Successfully deleted document {document_id} from SQLite and ChromaDB")
    return None


@router.get("/collections", response_model=list[dict])
def get_vector_collections():
    """
    List available ChromaDB collections.
    """
    return list_collections()


@router.post("/search", response_model=list[VectorQueryResult])
def search_vector_store(
    payload: VectorQueryRequest,
):
    """
    Query the vector database using a natural language query.

    - If category is provided, it is used as the collection name (normalized).
    - Otherwise, searches the default "documents" collection.
    """
    # Determine collection name from category
    if payload.category:
        collection_name = payload.category.lower().replace(" ", "_")
    else:
        collection_name = "documents"

    try:
        results = query_collection(
            query_text=payload.query,
            collection_name=collection_name,
            n_results=payload.top_k,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )

    # Chroma returns batched results; we only query with a single embedding
    ids = results.get("ids", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    response: list[VectorQueryResult] = []
    for idx, doc_id in enumerate(ids):
        response.append(
            VectorQueryResult(
                id=doc_id,
                document=documents[idx],
                score=float(distances[idx]),
                metadata=metadatas[idx] if metadatas and idx < len(metadatas) else {},
            )
        )

    return response


@router.post("/{document_id}/refine-description", response_model=DocumentResponse)
def refine_document_description(
    document_id: int,
    db: Session = Depends(get_db),
):
    """
    Refine/regenerate the document description using OpenAI API.
    This implements the feedback loop for description improvement.
    """
    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document with id {document_id} not found"
        )
    
    # Refine the description
    print(f"Refining description for document {document_id}: {document.title}")
    refined_description = refine_description(
        current_description=document.description or f"Document: {document.title}",
        title=document.title,
        category=document.category,
        openai_api_key=settings.openai_api_key
    )
    
    # Update the document with refined description
    document.description = refined_description
    db.commit()
    db.refresh(document)
    
    print(f"Refined description: {refined_description[:100]}...")
    
    return document


def format_answer_for_slack(answer: str) -> str:
    """
    Convert markdown-formatted answer to Slack message format.
    Slack supports markdown but with some differences from standard markdown.
    """
    # Start with the original answer
    slack_message = answer
    
    # Convert markdown headings to Slack-friendly format
    # ### Heading -> *Heading* (bold, with spacing)
    slack_message = re.sub(r'^### (.+)$', r'\n*\1*\n', slack_message, flags=re.MULTILINE)
    # #### Heading -> *Heading* (bold, inline)
    slack_message = re.sub(r'^#### (.+)$', r'*\1*', slack_message, flags=re.MULTILINE)
    # ## Heading -> *Heading* (bold, with spacing)
    slack_message = re.sub(r'^## (.+)$', r'\n*\1*\n', slack_message, flags=re.MULTILINE)
    
    # Convert **bold** to *bold* (Slack uses single asterisks)
    slack_message = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', slack_message)
    
    # Convert bullet points - preserve indentation but use Slack-friendly bullets
    # Handle nested lists (3 spaces = 1 level, 6 spaces = 2 levels)
    slack_message = re.sub(r'^      - ', r'        • ', slack_message, flags=re.MULTILINE)  # 2nd level
    slack_message = re.sub(r'^   - ', r'      • ', slack_message, flags=re.MULTILINE)  # 1st level
    slack_message = re.sub(r'^- ', r'• ', slack_message, flags=re.MULTILINE)  # Top level
    
    # Format References section clearly with separator
    slack_message = re.sub(
        r'^### References$',
        r'\n─────────────────────────\n*References:*\n',
        slack_message,
        flags=re.MULTILINE
    )
    
    # Add horizontal rule equivalent (Slack doesn't have <hr>, use dashes)
    slack_message = re.sub(r'^---$', r'─────────────────────────', slack_message, flags=re.MULTILINE)
    
    # Clean up any extra blank lines (more than 2 consecutive)
    slack_message = re.sub(r'\n{3,}', r'\n\n', slack_message)
    
    # Ensure proper spacing at start and end
    slack_message = slack_message.strip()
    
    return slack_message


def convert_json_to_toon_and_show_savings(data: any, call_name: str, data_name: str = "Data") -> tuple[str, int, int]:
    """
    Convert JSON data to TOON format and display token savings.
    Returns: (toon_string, original_tokens, toon_tokens)
    """
    enc = tiktoken.get_encoding("cl100k_base")
    
    # Convert to JSON string for comparison
    json_str = json.dumps(data, indent=2)
    original_tokens = len(enc.encode(json_str))
    
    # Convert to TOON format
    try:
        toon_str = encode(data)
        # Ensure it's a string
        if isinstance(toon_str, bytes):
            toon_str = toon_str.decode('utf-8')
        elif not isinstance(toon_str, str):
            toon_str = str(toon_str)
    except Exception as e:
        print(f"Warning: Could not encode {data_name} to TOON for {call_name}: {e}. Using JSON.")
        toon_str = json_str
    
    # Count tokens for TOON format (ensure it's a string)
    if not isinstance(toon_str, str):
        toon_str = str(toon_str)
    toon_tokens = len(enc.encode(toon_str))
    
    return toon_str, original_tokens, toon_tokens


@router.post("/ask", response_model=AskResponse)
def ask_question(
    request: AskRequest,
    db: Session = Depends(get_db),
):
    import time
    import json
    start_time = time.time()
    """
    Answer a user's question using AI-guided multi-stage retrieval with new flow:
    
    Step 1: Pass all categories/collections with descriptions → AI decides which collections to fetch,
            modifies user query, generates top_k_documents
    
    Step 2: Filter docs by collection name and query from Step 1 → Search master_docs collection
            → For each relevant doc, generate per-document parameters (top_k_chunks_per_document,
            search_strategy, response_length, response_depth, estimated_tokens)
    
    Step 3: Find relevant chunks based on document name, collection name, top_k_chunks_per_document
            (dynamic per doc from Step 2) → Choose model selection
    
    Step 4: Generate final answer
    
    All JSON data is converted to TOON format for token efficiency.
    Comprehensive token usage and savings logging is included.
    """
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OpenAI API key not configured"
        )
    
    client = OpenAI(api_key=settings.openai_api_key)
    
    # Initialize token tracking and API call responses
    token_usage_tracker = {
        "calls": [],
        "total_json_tokens": 0,
        "total_toon_tokens": 0,
        "total_savings": 0,
        "total_savings_percent": 0.0
    }
    api_call_responses = []  # Store responses from each API call
    
    try:
        # Initialize token encoder
        enc = tiktoken.get_encoding("cl100k_base")
        
        # ====================================================================
        # CONVERSATION CONTEXT ANALYSIS
        # Analyze conversation history and query to determine optimal settings
        # ====================================================================
        conversation_history = request.conversation_history or []
        conversation_length = len(conversation_history)
        is_follow_up = conversation_length > 0
        is_clarification = any(
            word in request.question.lower() 
            for word in ["what do you mean", "can you explain", "clarify", "elaborate", "more details", "again"]
        )
        
        # Analyze query complexity for dynamic model/token selection
        query_length = len(request.question)
        question_words = ["what", "how", "why", "when", "where", "who", "which", "explain", "describe", "tell me"]
        has_complex_question = any(word in request.question.lower() for word in question_words)
        is_simple_greeting = request.question.lower().strip() in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening"]
        
        print(f"Conversation Context: History={conversation_length} messages, Follow-up={is_follow_up}, Clarification={is_clarification}, Complex={has_complex_question}")
        
        # ====================================================================
        # STEP 1: Get all categories/collections with descriptions
        # AI decides which collections to fetch, modifies query, generates top_k_documents
        # ====================================================================
        print(f"Step 1: Getting all categories/collections and determining which to fetch...")
        
        # Get all active categories with descriptions from SQLite
        categories = db.query(Category).filter(Category.is_active == True).all()
        categories_data = []
        for cat in categories:
            categories_data.append({
                "collection_name": cat.collection_name,
                "category_name": cat.name,
                "description": cat.description or "No description available"
            })
        
        # Convert categories to TOON format for Step 1
        categories_toon, step1_categories_json, step1_categories_toon = convert_json_to_toon_and_show_savings(
            categories_data, "Step 1: Collection Selection", "Categories"
        )
        
        # Build Step 1 prompt with categories in TOON format
        if not categories_data:
            # No categories available, return early
            return AskResponse(
                answer="No categories are available. Please create categories first before asking questions.",
                token_usage=None,
                toon_savings=None,
                api_calls=[]
            )
        
        # Build conversation context for Step 1
        conversation_context = ""
        if conversation_history:
            conversation_context = "\n\nPrevious Conversation Context:\n"
            for i, msg in enumerate(conversation_history[-3:]):  # Last 3 messages for context
                role = msg.get("role", "user")
                content = msg.get("content", "")
                conversation_context += f"{role.capitalize()}: {content}\n"
            conversation_context += "\nNote: This is a follow-up question. Consider the conversation context when selecting collections."
        
        step1_prompt = f"""CRITICAL FIRST STEP: Determine if the user's question is actually RELATED to any of the available collections/data.

You have access to the following Categories/Collections (in TOON format):
{categories_toon}

User Question: {request.question}{conversation_context}

ANALYSIS PROCESS:

1. **RELEVANCE CHECK (MOST IMPORTANT)**:
   - First, determine if the question is AT ALL related to the available collections/data
   - Compare the question against each category's name and description
   - Questions that are NOT related include:
     * Simple greetings ONLY: "hi", "hello", "hey", "good morning", etc. (without any content question)
     * Pure conversational ONLY: "thanks", "okay", "bye", etc. (without any content question)
     * General knowledge questions completely unrelated to your collections (e.g., "what is the capital of France")
   - Questions that ARE RELATED and MUST proceed (proceed_to_step2 = true):
     * **Meta-queries about your data**: "what data do you have", "what documents are available", "what information is in your database", "what can you help with"
     * **Questions about collections**: "what collections do you have", "how many documents in [collection]", "what's in [collection]"
     * **Questions about document names/titles**: "what are the names of documents", "list the documents", "what documents are available"
     * **Questions asking "what is [topic]" or "tell me about [topic]"**: ALWAYS proceed - search documents to find information about the topic, even if topic isn't explicitly in category descriptions. The topic might be in document content/titles.
     * **Questions about topics mentioned in category descriptions**: Match against category names/descriptions
     * **Questions seeking information that could be in those collections**: Any query that might have answers in your documents - BE LIBERAL here
     * **Follow-up questions about previously discussed topics from documents**
     * **ANY question containing words/phrases that match category names or descriptions**
     * **ANY question asking for explanation/definition/information about something**: "what is X", "explain X", "tell me about X" - these should search documents

2. **IF NOT RELATED TO COLLECTIONS**:
   - Set proceed_to_step2 = false
   - Set selected_collections = [] (empty list)
   - Provide an appropriate direct_answer:
     * For greetings: Friendly greeting like "Hello! 👋 I'm ASKMOJO, your AI assistant. I can help you find information from your documents. What would you like to know?"
     * For conversational: Appropriate conversational response
     * For unrelated questions: "I can only answer questions related to the documents in my knowledge base. Based on your available collections, I can help with [mention relevant topics from collections]. How can I assist you?"

3. **IF RELATED TO COLLECTIONS**:
   - **ALWAYS proceed if the question asks about:**
     * What data/documents you have → Select all relevant collections, refine query to find document metadata/titles
     * Document names/titles → Select relevant collections, refine to search for document titles
     * Content in collections (e.g., "what is aftershoot") → Select relevant collections, refine query appropriately
     * Count of documents → Select relevant collections, refine query to find document count
   - Select one or more relevant category collections from the available list above
   - For meta-queries about data/documents, you may need to select ALL relevant collections
   - Refine/modify the query optimized for searching the selected collections:
     * "what data ask mojo have" → "what documents and information are available"
     * "what is after shoot" → "aftershoot information" (search documents)
     * "how many business proposals" → "business proposals" (to count/search them)
     * "what are the names of business proposals" → "business proposal titles and names"
   - Determine how many documents to retrieve (top_k_documents):
     * For meta-queries about document names/counts: Use higher number (10-50) to get comprehensive results
     * For specific content questions: Use typical range (2-10)
   - Set proceed_to_step2 = true
   - Provide a refined_question optimized for document search

CRITICAL RULES:
- DO NOT select "master_docs" - it is a special internal collection, not a category
- DO NOT proceed with document retrieval ONLY for pure greetings/conversational (no content question): "hi", "hello", "thanks", "bye"
- **MUST proceed (proceed_to_step2 = true) if question asks about:**
  * Available data/documents ("what data", "what documents", "what information")
  * Document names/titles ("what are the names", "list documents")
  * Content that might be in collections - **ALWAYS proceed for "what is X" type questions** - search documents even if X isn't in category descriptions
  * Topics matching category names/descriptions
  * Any question starting with "what is", "tell me about", "explain", "what are" - these should search documents
- **Be VERY liberal with relevance**: If a question COULD be answered from your collections, proceed with document retrieval. When in doubt, proceed.
- If question is truly unrelated (no connection to any collection), set proceed_to_step2 = false

Respond in JSON format:
{{
    "selected_collections": [<list of collection_name strings from the available categories above, e.g., ["proposals", "contracts"]>],
    "refined_question": "<modified/refined question text optimized for the selected collections>",
    "top_k_documents": <number of documents to retrieve from master collection, typically 2-10>,
    "proceed_to_step2": true/false,
    "reasoning": "<brief explanation of which collections were selected and why, and how the query was refined>",
    "direct_answer": "<if proceed_to_step2 is false and query can be answered directly, provide answer here, otherwise null>",
    "skip_reason": "<if proceed_to_step2 is false, explain why>"
}}"""

        # Track Step 1 prompt tokens
        step1_prompt_tokens = len(enc.encode(step1_prompt))
        
        # Calculate dynamic max_tokens for Step 1 based on context and query complexity
        # Let the model decide based on available data and query requirements
        num_categories = len(categories_data)
        query_length = len(request.question)
        conversation_context_length = len(conversation_context) if conversation_context else 0
        
        # Base tokens for JSON structure + reasoning
        base_tokens = 400
        # Scale by number of categories (more categories = more reasoning needed)
        category_factor = min(num_categories * 30, 500)
        # Scale by query complexity (longer queries may need more detailed reasoning)
        query_complexity = min(query_length / 30, 300)
        # Add context factor if conversation history exists
        context_factor = min(conversation_context_length / 5, 200)
        
        # Calculate dynamic max_tokens (generous to allow model to decide)
        step1_max_tokens = base_tokens + category_factor + int(query_complexity) + int(context_factor)
        # Cap at reasonable limit (4096 for JSON responses, but allow more for complex cases)
        step1_max_tokens = max(500, min(step1_max_tokens, 8192))  # Generous cap, model can use what it needs
        
        print(f"Step 1 Dynamic Token Limit: {step1_max_tokens} (categories={num_categories}, query_length={query_length}, context_length={conversation_context_length})")
        
        # Call OpenAI for Step 1 - model decides based on context and complexity
        step1_response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert information retrieval specialist with deep analytical capabilities. Your CRITICAL role is to determine if a user's question is related to available collections/data. IMPORTANT: Questions asking about 'what data/documents you have', 'document names', 'how many documents', or any content that could be in collections (e.g., 'what is [topic]') MUST proceed with document retrieval (proceed_to_step2 = true). ONLY set proceed_to_step2 = false for pure greetings ('hi', 'hello') or truly unrelated questions (e.g., 'what is the capital of France' when your collections are about business). Be LIBERAL with relevance - if a question could be answered from collections, proceed. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and analysis."
                },
                {
                    "role": "user",
                    "content": step1_prompt
                }
            ],
            response_format={"type": "json_object"},
            temperature=0.2,  # Lower temperature for more precise, intelligent collection selection
            max_tokens=step1_max_tokens  # Dynamic limit based on context - model decides actual length needed
        )
        
        # Track Step 1 response tokens
        step1_response_tokens = step1_response.usage.total_tokens if hasattr(step1_response, 'usage') else 0
        step1_total_json = step1_prompt_tokens + step1_response_tokens + step1_categories_json
        step1_total_toon = step1_prompt_tokens + step1_response_tokens + step1_categories_toon
        step1_savings = step1_total_json - step1_total_toon
        step1_savings_percent = (step1_savings / step1_total_json * 100) if step1_total_json > 0 else 0
        
        # Parse Step 1 response
        step1_data = json.loads(step1_response.choices[0].message.content)
        
        # Extract Step 1 results (now includes decision) - DO THIS BEFORE LOGGING
        selected_collections = step1_data.get("selected_collections", [])
        refined_question = step1_data.get("refined_question", request.question)
        top_k_docs = step1_data.get("top_k_documents", 5)
        proceed_to_step2 = step1_data.get("proceed_to_step2", True)
        step1_reasoning = step1_data.get("reasoning", "")
        direct_answer = step1_data.get("direct_answer")
        skip_reason = step1_data.get("skip_reason")
        
        # Track Step 1
        token_usage_tracker["calls"].append({
            "call_name": "Step 1: Collection Selection, Query Refinement & Decision",
            "json_tokens": step1_total_json,
            "toon_tokens": step1_total_toon,
            "savings": step1_savings,
            "savings_percent": step1_savings_percent
        })
        
        # Store Step 1 response with complete information
        step1_response_data = {
            "call_name": "Step 1: Collection Selection, Query Refinement & Decision",
            "request_prompt": step1_prompt,  # Store full prompt
            "response_content": step1_data,  # Full response JSON
            "model_used": "gpt-4o-mini",
            "tokens_used": step1_total_toon,
            "tokens_without_toon": step1_total_json,
            "savings": step1_savings,
            "savings_percent": step1_savings_percent,
            "max_tokens": step1_max_tokens,  # Dynamic limit based on context
            "temperature": 0.2,  # Lower temperature for more precise collection selection
            "selected_collections": selected_collections,
            "refined_question": refined_question,
            "top_k_documents": top_k_docs,
            "proceed_to_step2": proceed_to_step2,
            "reasoning": step1_reasoning
        }
        api_call_responses.append(step1_response_data)
        
        # Check if we should proceed (decision now included in Step 1 response) - DO THIS FIRST
        if not proceed_to_step2:
            print(f"Step 1 Decision: NOT proceeding to Step 2")
            print(f"  Reason: {skip_reason or 'Unknown'}")
            print(f"  Reasoning: {step1_reasoning}")
            
            # If AI provided a direct answer, use it
            if direct_answer:
                print(f"  Using AI-provided direct answer: {direct_answer[:100]}...")
                return AskResponse(
                    answer=direct_answer,
                    token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                    toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                    api_calls=api_call_responses
                )
            else:
                # Generate appropriate response based on query type
                question_lower = request.question.lower().strip()
                if question_lower in ["hi", "hello", "hey", "good morning", "good afternoon", "good evening", "good night"]:
                    response = "Hello! 👋 I'm ASKMOJO, your AI assistant. I can help you find information from your documents. What would you like to know?"
                elif question_lower in ["thanks", "thank you"]:
                    response = "You're welcome! Feel free to ask if you need anything else."
                elif question_lower in ["bye", "goodbye", "see you"]:
                    response = "Goodbye! Feel free to come back if you need any help."
                else:
                    # Question not related to available collections
                    category_names = [cat.name for cat in categories] if categories else []
                    response = f"I can only answer questions related to the documents in my knowledge base. Based on your available collections, I can help with: {', '.join(category_names) if category_names else 'topics covered in your documents'}. How can I assist you?"
                
                return AskResponse(
                    answer=response,
                    token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                    toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                    api_calls=api_call_responses
                )
        
        # If proceeding, validate and filter selected_collections
        # Remove "master_docs" if it was incorrectly selected
        if "master_docs" in selected_collections:
            print(f"Warning: AI incorrectly selected 'master_docs'. Removing it from selected collections.")
            selected_collections = [c for c in selected_collections if c != "master_docs"]
        
        # Get valid collection names from categories
        valid_collection_names = {cat.collection_name for cat in categories}
        
        # Filter to only include valid category collections
        selected_collections = [c for c in selected_collections if c in valid_collection_names]
        
        # If no valid collections selected but proceed_to_step2 is true, check if this is an error
        if not selected_collections and proceed_to_step2:
            print(f"Warning: No valid collections selected but proceed_to_step2 is true. This may indicate the query is not related to available collections.")
            # Don't use fallback - return appropriate response instead
            return AskResponse(
                answer=f"I couldn't find relevant collections for your question. Based on your available collections, I can help with: {', '.join([cat.name for cat in categories]) if categories else 'topics covered in your documents'}. Please ask a question related to these collections.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # Validate top_k_docs
        top_k_docs = max(1, min(50, top_k_docs))
        
        print(f"Step 1 Results:")
        print(f"  Selected Collections: {selected_collections}")
        print(f"  Refined Question: {refined_question}")
        print(f"  Top K Documents: {top_k_docs}")
        print(f"  Proceed to Step 2: {proceed_to_step2}")
        print(f"  Reasoning: {step1_reasoning}")
        
        print(f"Step 1 Decision: Proceeding to Step 2")
        
        # ====================================================================
        # STEP 2: Filter docs by collection name and query from Step 1
        # Search master_docs collection
        # For each relevant doc, generate per-document parameters
        # ====================================================================
        print(f"Step 2: Searching master_docs collection with refined query and filtering by selected collections...")
        
        # Search master_docs collection
        master_results = query_master_collection(
            query_text=refined_question,
            n_results=top_k_docs
        )
        
        if not master_results.get("ids") or not master_results["ids"][0]:
            return AskResponse(
                answer="I couldn't find any relevant documents to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # Extract document IDs and filter by selected collections
        document_ids = [int(doc_id) for doc_id in master_results["ids"][0]]
        
        # Get full document info from SQLite with category information
        documents = db.query(Document).filter(Document.id.in_(document_ids)).all()
        doc_dict = {doc.id: doc for doc in documents}
        
        # Filter documents by selected collections
        filtered_documents = []
        for doc_id in document_ids:
            if doc_id not in doc_dict:
                continue
            doc = doc_dict[doc_id]
            
            # Get collection name from category
            collection_name = None
            if doc.category_id:
                category = db.query(Category).filter(Category.id == doc.category_id).first()
                if category:
                    collection_name = category.collection_name
            elif doc.category:
                collection_name = doc.category.lower().replace(" ", "_").replace("-", "_")
            
            # Only include if collection is in selected_collections (or if no collections selected, include all)
            if not selected_collections or (collection_name and collection_name in selected_collections):
                filtered_documents.append(doc_id)
        
        if not filtered_documents:
            return AskResponse(
                answer="I couldn't find any relevant documents in the selected collections to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        print(f"  Found {len(filtered_documents)} documents in selected collections")
        
        # Check if this is a meta-query about document counts, names, or available data
        question_lower = refined_question.lower()
        
        # For meta-queries about "what data/information do you have" - list all collections and counts
        if any(keyword in question_lower for keyword in ["what data", "what information", "what do you have", "what can you help"]) and not any(word in question_lower for word in ["is", "about", "regarding"]):
            # Get all collections with their document counts
            collection_info = []
            for cat in categories:
                doc_count = db.query(Document).filter(Document.category_id == cat.id).count()
                if doc_count > 0:
                    collection_info.append(f"{cat.name}: {doc_count} document{'s' if doc_count != 1 else ''}")
            
            if collection_info:
                answer_text = "I have access to the following collections:\n\n" + "\n".join(f"• {info}" for info in collection_info) + "\n\nWhat would you like to know about these documents?"
            else:
                answer_text = "I don't have any documents in my knowledge base yet. Please upload documents to get started."
            
            return AskResponse(
                answer=answer_text,
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        # For meta-queries about counts, get actual count from database
        if any(keyword in question_lower for keyword in ["how many", "count of", "number of", "total"]) and any(word in question_lower for word in ["document", "proposal", "collection"]):
            if selected_collections:
                category_ids = [cat.id for cat in categories if cat.collection_name in selected_collections]
                if category_ids:
                    doc_count = db.query(Document).filter(Document.category_id.in_(category_ids)).count()
                    collection_name_display = [cat.name for cat in categories if cat.collection_name in selected_collections]
                    collection_name_display = collection_name_display[0] if collection_name_display else selected_collections[0] if selected_collections else "documents"
                    
                    return AskResponse(
                        answer=f"The {collection_name_display.lower()} collection contains a total of {doc_count} document{'s' if doc_count != 1 else ''}.",
                        token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                        toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                        api_calls=api_call_responses
                    )
        
        # For meta-queries about document names/titles, get actual titles from database
        if any(keyword in question_lower for keyword in ["what are the names", "list the", "name of"]) and any(word in question_lower for word in ["document", "proposal", "collection"]):
            if selected_collections:
                category_ids = [cat.id for cat in categories if cat.collection_name in selected_collections]
                if category_ids:
                    all_docs = db.query(Document).filter(Document.category_id.in_(category_ids)).order_by(Document.title).all()
                    if all_docs:
                        doc_titles = [doc.title for doc in all_docs]
                        if len(doc_titles) > 50:
                            titles_text = ", ".join(doc_titles[:50]) + f", and {len(doc_titles) - 50} more documents."
                        else:
                            titles_text = ", ".join(doc_titles) + "."
                        
                        collection_name_display = [cat.name for cat in categories if cat.collection_name in selected_collections]
                        collection_name_display = collection_name_display[0] if collection_name_display else selected_collections[0] if selected_collections else "documents"
                        
                    return AskResponse(
                        answer=f"The documents available in the {collection_name_display.lower()} collection include: {titles_text}",
                            token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                            toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                            api_calls=api_call_responses
                        )
        
        # Build document summaries for Step 2 AI call (in TOON format)
        master_summary = []
        for idx, doc_id in enumerate(document_ids):
            if doc_id not in filtered_documents:
                continue
            if doc_id not in doc_dict:
                continue
            doc = doc_dict[doc_id]
            
            # Get collection name
            collection_name = None
            if doc.category_id:
                category = db.query(Category).filter(Category.id == doc.category_id).first()
                if category:
                    collection_name = category.collection_name
            elif doc.category:
                collection_name = doc.category.lower().replace(" ", "_").replace("-", "_")
            
            master_summary.append({
                "document_id": doc_id,
                "title": doc.title,
                "collection_name": collection_name or "documents",
                "description": doc.description or "",
                "relevance_score": float(master_results["distances"][0][idx]) if master_results.get("distances") else 0.0
            })
        
        # Convert to TOON for Step 2
        master_summary_toon, step2_summaries_json, step2_summaries_toon = convert_json_to_toon_and_show_savings(
            master_summary, "Step 2: Per-Document Parameter Generation", "Document Summaries"
        )
        
        # Step 2: Generate per-document parameters AND decide if we can answer from summaries
        # Enhanced with intelligent reasoning for better parameter selection
        step2_prompt = f"""You are an expert at information retrieval and question analysis. Your task is to intelligently determine the optimal parameters for answering the user's question with maximum accuracy and relevance.

Analyze the question deeply to understand:
- What type of information is needed (factual, analytical, procedural, comparative, evaluative, etc.)
- The complexity and scope of the question
- Whether the question requires specific details, broad overviews, or both
- The level of depth needed for a comprehensive answer
- Implicit information needs (what the user really wants to know, not just what they asked)

For each relevant document found, intelligently determine the optimal parameters for retrieving chunks and generating the answer.
ALSO evaluate if the document summaries are sufficient to answer the question without chunk retrieval (be very conservative—only skip chunk retrieval if summaries genuinely contain all necessary detail).

User Question: {refined_question}

Relevant Documents (in TOON format):
{master_summary_toon}

For each document, intelligently determine:
1. **top_k_chunks_per_document**: 
   - For simple factual questions: 3-5 chunks may suffice
   - For analytical/comparative questions: 8-12 chunks for thorough analysis
   - For comprehensive questions requiring multiple perspectives: 12-15 chunks
   - For questions about processes/procedures: 10-15 chunks to capture complete flow
   - Set to 0 ONLY if document summaries truly contain enough detail to answer fully (rare)
   
2. **search_strategy**: 
   - "selective": For specific, targeted questions where precision matters
   - "comprehensive": For questions requiring multiple perspectives, comparisons, or exhaustive coverage
   - Consider: Does the question need breadth or depth? Does it compare multiple aspects?
   
3. **response_length**: 
   - "brief": Simple, direct questions (1-2 paragraphs)
   - "medium": Standard questions needing context (2-4 paragraphs)
   - "detailed": Complex questions requiring thorough explanation (4-6 paragraphs with structure)
   - "comprehensive": Questions requiring extensive coverage (multiple sections, detailed analysis)
   
4. **response_depth**: 
   - "high-level": Overview questions, strategic insights
   - "moderate": Balanced questions needing context and specifics
   - "deep": Analytical questions requiring thorough investigation
   - "exhaustive": Questions requiring complete, in-depth coverage
   
5. **estimated_tokens**: 
   - Base estimate: 500-2000 for brief, 2000-4000 for medium, 4000-7000 for detailed, 7000-10000 for comprehensive
   - Add 20-30% buffer for intelligent synthesis and analysis
   - Consider: More documents = potentially more synthesis = more tokens

Intelligent Considerations:
- Document relevance score (lower = more relevant = needs more chunks)
- Question type: Factual, analytical, procedural, comparative, evaluative
- Question complexity: Simple fact lookup vs. multi-part analysis
- Information density: Are document summaries rich or sparse?
- User intent: Do they need quick answer or thorough analysis?
- Can summaries alone answer? Only if they contain sufficient detail—be conservative

ALSO evaluate overall:
- Can the question be answered from document summaries alone? (proceed_to_step3)
- If summaries are sufficient, provide a direct answer (answer_from_summaries)

Respond in JSON format:
{{
    "proceed_to_step3": true/false,
    "answer_from_summaries": "<if proceed_to_step3 is false and summaries are sufficient, provide answer here, otherwise null>",
    "skip_reason": "<if proceed_to_step3 is false, explain why>",
    "confidence": "high|medium|low - confidence in answer quality from summaries",
    "document_configs": [
        {{
            "document_id": <document_id>,
            "top_k_chunks_per_document": <number (0 if summaries sufficient, otherwise 5-15)>,
            "search_strategy": "selective|comprehensive",
            "response_length": "brief|medium|detailed|comprehensive",
            "response_depth": "high-level|moderate|deep|exhaustive",
            "estimated_tokens": <number>,
            "reasoning": "<brief explanation for this document>"
        }},
        ...
    ]
}}"""

        # Track Step 2 prompt tokens
        step2_prompt_tokens = len(enc.encode(step2_prompt))
        
        # Calculate dynamic max_tokens for Step 2 based on context and query complexity
        # Let the model decide based on number of documents and query requirements
        num_documents = len(master_summary)
        query_length = len(refined_question)
        
        # Base tokens for JSON structure + reasoning
        base_tokens = 600
        # Scale by number of documents (more documents = more parameter decisions needed)
        document_factor = min(num_documents * 150, 2000)
        # Scale by query complexity (complex queries may need more detailed reasoning per document)
        query_complexity = min(query_length / 20, 400)
        # Factor for detailed parameter reasoning (top_k_chunks, strategies, etc.)
        parameter_reasoning_factor = min(num_documents * 100, 1500)
        
        # Calculate dynamic max_tokens (generous to allow model to decide)
        step2_max_tokens = base_tokens + document_factor + int(query_complexity) + parameter_reasoning_factor
        # Cap at reasonable limit (generous for complex cases with many documents)
        step2_max_tokens = max(800, min(step2_max_tokens, 12288))  # Very generous cap, model can use what it needs
        
        print(f"Step 2 Dynamic Token Limit: {step2_max_tokens} (documents={num_documents}, query_length={query_length})")
        
        # Call OpenAI for Step 2 - model decides based on context and complexity
        step2_response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert information retrieval specialist with advanced analytical capabilities. Your role is to intelligently determine optimal retrieval parameters by deeply understanding question complexity, information needs, and document relevance. Use sophisticated reasoning to evaluate whether summaries are sufficient (be conservative—only skip chunk retrieval if summaries truly contain all needed detail). Apply critical thinking to optimize parameters for maximum answer quality. Always respond with valid JSON. Use as many tokens as you need to provide thorough reasoning and detailed parameter selection based on the complexity of the question and number of documents."
                    },
                    {
                        "role": "user",
                    "content": step2_prompt
                    }
                ],
                response_format={"type": "json_object"},
            temperature=0.25,  # Lower temperature for more precise, intelligent parameter selection
            max_tokens=step2_max_tokens  # Dynamic limit based on context - model decides actual length needed
        )
        
        # Track Step 2 response tokens
        step2_response_tokens = step2_response.usage.total_tokens if hasattr(step2_response, 'usage') else 0
        step2_total_json = step2_prompt_tokens + step2_response_tokens + step2_summaries_json
        step2_total_toon = step2_prompt_tokens + step2_response_tokens + step2_summaries_toon
        step2_savings = step2_total_json - step2_total_toon
        step2_savings_percent = (step2_savings / step2_total_json * 100) if step2_total_json > 0 else 0
        
        # Parse Step 2 response (now includes decision)
        step2_data = json.loads(step2_response.choices[0].message.content)
        
        # Extract decision and configs (now included in Step 2 response) - DO THIS BEFORE LOGGING
        proceed_to_step3 = step2_data.get("proceed_to_step3", True)
        answer_from_summaries = step2_data.get("answer_from_summaries")
        doc_configs = step2_data.get("document_configs", [])
        
        # Track Step 2
        token_usage_tracker["calls"].append({
            "call_name": "Step 2: Per-Document Parameter Generation & Decision",
            "json_tokens": step2_total_json,
            "toon_tokens": step2_total_toon,
            "savings": step2_savings,
            "savings_percent": step2_savings_percent
        })
        
        # Store Step 2 response with complete information
        step2_response_data = {
            "call_name": "Step 2: Per-Document Parameter Generation & Decision",
            "request_prompt": step2_prompt,  # Store full prompt
            "response_content": step2_data,  # Full response JSON
            "model_used": "gpt-4o-mini",
            "tokens_used": step2_total_toon,
            "tokens_without_toon": step2_total_json,
            "savings": step2_savings,
            "savings_percent": step2_savings_percent,
            "max_tokens": step2_max_tokens,  # Dynamic limit based on context
            "temperature": 0.25,  # Lower temperature for more precise parameter selection
            "proceed_to_step3": proceed_to_step3,
            "document_configs": doc_configs,
            "documents_found": len(master_summary) if 'master_summary' in locals() else 0
        }
        api_call_responses.append(step2_response_data)
        
        if not proceed_to_step3:
            print(f"Step 2 Decision: NOT proceeding to Step 3")
            print(f"  Reason: {step2_data.get('skip_reason', 'Unknown')}")
            print(f"  Confidence: {step2_data.get('confidence', 'N/A')}")
            
            if answer_from_summaries:
                # Use the answer provided by Step 2
                return AskResponse(
                    answer=answer_from_summaries,
                    token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                    toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                    api_calls=api_call_responses
                )
            
            # If no answer from summaries, return basic response
            return AskResponse(
                answer=f"Based on the available documents, I found {len(master_summary)} relevant document(s): {', '.join([d['title'] for d in master_summary[:3]])}. However, more detailed information is needed to fully answer your question. {step2_data.get('skip_reason', 'Please rephrase or provide more context.')}",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        print(f"Step 2 Decision: Proceeding to Step 3 (chunk retrieval)")
        print(f"  Reasoning: {step2_data.get('skip_reason', 'N/A')}")
        print(f"  Confidence: {step2_data.get('confidence', 'N/A')}")
        
        # Create per-document config dictionary (doc_configs already extracted above)
        doc_config_dict = {config["document_id"]: config for config in doc_configs}
        
        # Default values for documents not in config
        default_config = {
            "top_k_chunks_per_document": 10,
            "search_strategy": "selective",
            "response_length": "medium",
            "response_depth": "moderate",
            "estimated_tokens": 3000
        }
        
        print(f"Step 2 Results: Generated parameters for {len(doc_configs)} documents")
        
        # ====================================================================
        # STEP 3: Find relevant chunks based on document name, collection name,
        # top_k_chunks_per_document (dynamic per doc from Step 2)
        # Choose model selection
        # ====================================================================
        print(f"Step 3: Retrieving chunks for each document with dynamic top_k...")
        
        relevant_documents = []
        source_chunks = []
        
        for doc_id in filtered_documents:
            if doc_id not in doc_dict:
                continue
            
            document = doc_dict[doc_id]
            
            # Get collection name
            collection_name = None
            if document.category_id:
                category = db.query(Category).filter(Category.id == document.category_id).first()
                if category:
                    collection_name = category.collection_name
            elif document.category:
                collection_name = document.category.lower().replace(" ", "_").replace("-", "_")
            else:
                collection_name = "documents"
            
            # Get per-document config (or use defaults)
            doc_config = doc_config_dict.get(doc_id, default_config)
            top_k_chunks = doc_config.get("top_k_chunks_per_document", 10)
            top_k_chunks = max(1, min(30, top_k_chunks))  # Clamp to reasonable range
            
            # Add document to relevant_documents
            doc_idx = document_ids.index(doc_id) if doc_id in document_ids else 0
            relevant_documents.append({
                "document_id": doc_id,
                "title": document.title,
                "collection_name": collection_name,
                "category": document.category or "documents",
                "description": document.description,
                "score": float(master_results["distances"][0][doc_idx]) if master_results.get("distances") and doc_idx < len(master_results["distances"][0]) else 0.0,
                "top_k_chunks": top_k_chunks,
                "response_length": doc_config.get("response_length", "medium"),
                "response_depth": doc_config.get("response_depth", "moderate"),
                "estimated_tokens": doc_config.get("estimated_tokens", 3000)
            })
            
            # Query collection for chunks
            try:
                chunk_results = query_collection(
                    query_text=refined_question,
                    collection_name=collection_name,
                    n_results=top_k_chunks
                )
                
                if chunk_results.get("ids") and chunk_results["ids"][0]:
                    chunks_found = 0
                    for chunk_idx, chunk_id in enumerate(chunk_results["ids"][0]):
                        chunk_metadata = chunk_results["metadatas"][0][chunk_idx] if chunk_results.get("metadatas") else {}
                        chunk_doc_id = chunk_metadata.get("document_id")
                        
                        # Convert to int for comparison
                        try:
                            if isinstance(chunk_doc_id, str):
                                chunk_doc_id = int(chunk_doc_id)
                            elif chunk_doc_id is None:
                                continue
                        except (ValueError, TypeError):
                            continue
                        
                        # Only include chunks from this document
                        if chunk_doc_id == doc_id:
                            chunks_found += 1
                            source_chunks.append(SourceChunk(
                                document_id=doc_id,
                                document_title=document.title,
                                category=document.category or "documents",
                                chunk_text=chunk_results["documents"][0][chunk_idx] if chunk_results.get("documents") else "",
                                page_number=chunk_metadata.get("page_number"),
                                chunk_index=chunk_metadata.get("chunk_index"),
                                score=float(chunk_results["distances"][0][chunk_idx]) if chunk_results.get("distances") else 0.0
                            ))
                    
                    print(f"  Document {doc_id} ({collection_name}): Found {chunks_found} chunks (top_k={top_k_chunks})")
            except Exception as e:
                print(f"  Error querying collection {collection_name} for document {doc_id}: {str(e)}")
                continue
        
        if not source_chunks and not relevant_documents:
            return AskResponse(
                answer="I couldn't find any relevant documents or chunks to answer your question.",
                token_usage=token_usage_summary if 'token_usage_summary' in locals() else None,
                toon_savings=toon_savings_breakdown if 'toon_savings_breakdown' in locals() else None,
                api_calls=api_call_responses
            )
        
        print(f"Step 3 Results: Found {len(relevant_documents)} documents with {len(source_chunks)} chunks")
        
        # ====================================================================
        # STEP 3 DATA QUALITY ASSESSMENT: Evaluate locally (no API call)
        # ====================================================================
        # Evaluate data quality locally based on retrieved chunks
        if source_chunks and relevant_documents:
            avg_chunks_per_doc = len(source_chunks) / len(relevant_documents)
            
            # Assess data quality based on metrics
            if avg_chunks_per_doc >= 5 and len(source_chunks) >= 10:
                data_quality = "excellent"
                confidence_score = 85
            elif avg_chunks_per_doc >= 3 and len(source_chunks) >= 5:
                data_quality = "good"
                confidence_score = 75
            elif avg_chunks_per_doc >= 1 and len(source_chunks) >= 3:
                data_quality = "sufficient"
                confidence_score = 65
            else:
                data_quality = "insufficient"
                confidence_score = 50
            
            print(f"Step 3 Data Quality Assessment:")
            print(f"  Average Chunks per Document: {avg_chunks_per_doc:.1f}")
            print(f"  Total Chunks: {len(source_chunks)}")
            print(f"  Data Quality: {data_quality}")
            print(f"  Confidence Score: {confidence_score}/100")
        else:
            data_quality = "insufficient"
            confidence_score = 30
            print(f"Step 3 Data Quality Assessment: No chunks or documents found")
        
        step3_decision = {
            "proceed_to_step4": True,
            "data_quality": data_quality,
            "confidence_score": confidence_score
        }
        
        # Step 3: Choose model selection based on documents and question complexity
        # Aggregate response requirements from all documents
        all_response_lengths = [doc.get("response_length", "medium") for doc in relevant_documents]
        all_response_depths = [doc.get("response_depth", "moderate") for doc in relevant_documents]
        all_estimated_tokens = [doc.get("estimated_tokens", 3000) for doc in relevant_documents]
        
        # Determine overall response characteristics (use most demanding)
        length_priority = {"comprehensive": 4, "detailed": 3, "medium": 2, "brief": 1}
        depth_priority = {"exhaustive": 4, "deep": 3, "moderate": 2, "high-level": 1}
        
        response_length = max(all_response_lengths, key=lambda x: length_priority.get(x, 2))
        response_depth = max(all_response_depths, key=lambda x: depth_priority.get(x, 2))
        estimated_tokens = max(all_estimated_tokens) if all_estimated_tokens else 3000
        estimated_tokens = max(500, min(10000, estimated_tokens))
        
        # Adjust response parameters based on data quality from Step 3 decision
        if step3_decision.get("data_quality") == "insufficient":
            # Reduce response length/depth if data quality is insufficient
            if length_priority.get(response_length, 2) > 2:
                response_length = "medium"
                print(f"  Adjusted response_length to 'medium' due to insufficient data quality")
            if depth_priority.get(response_depth, 2) > 2:
                response_depth = "moderate"
                print(f"  Adjusted response_depth to 'moderate' due to insufficient data quality")
            estimated_tokens = min(estimated_tokens, 2000)  # Cap tokens for insufficient data
        
        # ====================================================================
        # DYNAMIC MODEL SELECTION
        # Intelligently select model based on multiple factors
        # ====================================================================
        # Base model selection on complexity
        base_model_score = 0
        
        # Factor 1: Response requirements
        if length_priority.get(response_length, 2) >= 4:
            base_model_score += 2
        elif length_priority.get(response_length, 2) >= 3:
            base_model_score += 1
        
        if depth_priority.get(response_depth, 2) >= 4:
            base_model_score += 2
        elif depth_priority.get(response_depth, 2) >= 3:
            base_model_score += 1
        
        # Factor 2: Token requirements
        if estimated_tokens > 6000:
            base_model_score += 2
        elif estimated_tokens > 3000:
            base_model_score += 1
        
        # Factor 3: Query complexity
        if has_complex_question and query_length > 100:
            base_model_score += 1
        
        # Factor 4: Conversation context (follow-ups may need better understanding)
        if is_follow_up and conversation_length > 2:
            base_model_score += 1
        
        # Factor 5: Data quality (better model for insufficient data)
        if step3_decision.get("data_quality") == "insufficient":
            base_model_score += 1
        
        # Factor 6: Number of documents (more docs = more complex reasoning)
        if len(relevant_documents) > 5:
            base_model_score += 1
        
        # User preference override
        if request.model_preference:
            selected_model = request.model_preference
            print(f"Using user-specified model: {selected_model}")
        elif base_model_score >= 4:
            selected_model = "gpt-4o"  # Use GPT-4o for complex queries
        elif base_model_score >= 2:
            selected_model = "gpt-4o-mini"  # Use GPT-4o-mini for moderate complexity
        else:
            selected_model = "gpt-4o-mini"  # Default to GPT-4o-mini for simple queries
        
        print(f"Step 3 Model Selection: {selected_model} (score={base_model_score}, length={response_length}, depth={response_depth}, tokens={estimated_tokens})")
        
        # ====================================================================
        # DYNAMIC MAX TOKENS CALCULATION
        # Intelligently calculate max_tokens based on multiple factors
        # ====================================================================
        # Base token calculation
        base_max_tokens = estimated_tokens
        
        # Factor 1: Model capabilities
        if selected_model == "gpt-4o":
            # GPT-4o can handle longer responses, allow more headroom
            base_max_tokens = int(base_max_tokens * 1.2)
        else:
            # GPT-4o-mini, keep closer to estimate
            base_max_tokens = int(base_max_tokens * 1.1)
        
        # Factor 2: Conversation context (follow-ups may need more context)
        if is_follow_up:
            base_max_tokens = int(base_max_tokens * 1.15)  # 15% more for context
        
        # Factor 3: Response length requirements
        if response_length == "comprehensive":
            base_max_tokens = int(base_max_tokens * 1.3)
        elif response_length == "detailed":
            base_max_tokens = int(base_max_tokens * 1.2)
        elif response_length == "brief":
            base_max_tokens = int(base_max_tokens * 0.8)
        
        # Factor 4: Number of documents (more docs = potentially longer answer)
        if len(relevant_documents) > 3:
            base_max_tokens = int(base_max_tokens * 1.1)
        
        # Factor 5: Data quality (insufficient data = shorter answer)
        if step3_decision.get("data_quality") == "insufficient":
            base_max_tokens = int(base_max_tokens * 0.7)
        
        # Apply bounds
        min_tokens = 500
        max_tokens_limit = 16000 if selected_model == "gpt-4o" else 16000  # Both models support up to 16k
        dynamic_max_tokens = max(min_tokens, min(max_tokens_limit, base_max_tokens))
        
        # User override
        if request.max_tokens:
            dynamic_max_tokens = max(min_tokens, min(max_tokens_limit, request.max_tokens))
            print(f"Using user-specified max_tokens: {dynamic_max_tokens}")
        
        print(f"Dynamic Max Tokens: {dynamic_max_tokens} (base={estimated_tokens}, adjusted for model={selected_model}, context={is_follow_up}, length={response_length})")
        
        # ====================================================================
        # STEP 4: Generate final answer
        # ====================================================================
        print(f"Step 4: Generating final answer...")
        
        # Build document summaries and chunks in JSON format
        document_summaries = []
        for doc in relevant_documents:
                document_summaries.append({
                    "document_id": doc["document_id"],
                    "title": doc["title"],
                "collection_name": doc["collection_name"],
                    "category": doc.get("category"),
                "description": doc.get("description", ""),
                    "relevance_score": doc.get("score", 0.0)
                })
        
        chunks_json = []
        for chunk in source_chunks:
            chunks_json.append({
                "document_id": chunk.document_id,
                "document_title": chunk.document_title,
                "collection_name": chunk.category,
                "chunk_text": chunk.chunk_text,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "relevance_score": chunk.score
            })
        
        # Convert to TOON format for Step 4
        summaries_toon_str, step4_summaries_json, step4_summaries_toon = convert_json_to_toon_and_show_savings(
            document_summaries, "Step 4: Final Answer Generation", "Document Summaries"
        )
        
        if chunks_json:
            chunks_toon_str, step4_chunks_json, step4_chunks_toon = convert_json_to_toon_and_show_savings(
                chunks_json, "Step 4: Final Answer Generation", "Chunks"
            )
        else:
            chunks_toon_str = encode([])
            if isinstance(chunks_toon_str, bytes):
                chunks_toon_str = chunks_toon_str.decode('utf-8')
            elif not isinstance(chunks_toon_str, str):
                chunks_toon_str = str(chunks_toon_str)
            step4_chunks_json = len(enc.encode("[]"))
            step4_chunks_toon = len(enc.encode(chunks_toon_str))
        
        # Build response instructions
        length_instructions = {
            "brief": "Provide a concise, to-the-point answer. Keep it short and focused. Aim for 2-4 sentences or a brief list.",
            "medium": "Provide a moderately detailed answer with context. Include key points and explanations. Aim for 1-2 paragraphs or a well-structured list with brief explanations.",
            "detailed": "Provide a comprehensive, detailed answer with thorough explanations. Include context, examples where relevant, and multiple aspects of the topic. Aim for 3-5 paragraphs or a detailed structured response.",
            "comprehensive": "Provide an exhaustive, complete answer covering all relevant aspects. Include extensive context, detailed explanations, multiple perspectives, and comprehensive coverage. Aim for a substantial response with multiple sections."
        }
        
        depth_instructions = {
            "high-level": "Focus on overview, summaries, and high-level concepts. Avoid deep technical details.",
            "moderate": "Provide balanced detail with context and explanations. Include some specifics but maintain readability.",
            "deep": "Provide in-depth analysis with detailed explanations, examples, and thorough coverage of the topic.",
            "exhaustive": "Provide exhaustive coverage with all relevant details, comprehensive analysis, and complete information."
        }
        
        length_instruction = length_instructions.get(response_length, length_instructions["medium"])
        depth_instruction = depth_instructions.get(response_depth, depth_instructions["moderate"])
        
        chunks_note = ""
        if not chunks_json:
            chunks_note = "\nNOTE: No detailed chunks are available for this query. Use the Document Summaries to answer the question."
        
        # Build Step 4 prompt with TOON format (includes data quality awareness)
        # Get data quality from step3_decision (defined earlier)
        data_quality = step3_decision.get("data_quality", "sufficient")
        confidence_score = step3_decision.get("confidence_score", 70)
        
        data_quality_note = ""
        if data_quality == "insufficient":
            data_quality_note = f"\n⚠️ DATA QUALITY NOTE: The retrieved information has limited coverage ({len(source_chunks)} chunks for {len(relevant_documents)} documents, confidence: {confidence_score}/100). Provide the best answer possible with available information, but acknowledge limitations if the information is insufficient."
        elif data_quality == "sufficient":
            data_quality_note = f"\n✓ DATA QUALITY NOTE: The retrieved information has sufficient coverage ({len(source_chunks)} chunks for {len(relevant_documents)} documents, confidence: {confidence_score}/100). You can provide a comprehensive answer."
        elif data_quality == "excellent":
            data_quality_note = f"\n✓✓ DATA QUALITY NOTE: The retrieved information has excellent coverage ({len(source_chunks)} chunks for {len(relevant_documents)} documents, confidence: {confidence_score}/100). You can provide a detailed, comprehensive answer."
        elif data_quality == "good":
            data_quality_note = f"\n✓ DATA QUALITY NOTE: The retrieved information has good coverage ({len(source_chunks)} chunks for {len(relevant_documents)} documents, confidence: {confidence_score}/100). You can provide a comprehensive answer."
        
        step4_prompt = f"""You are an expert AI assistant with deep analytical capabilities, critical thinking skills, and the ability to synthesize complex information into clear, insightful answers. Your role is to provide intelligent, well-reasoned responses that demonstrate sophisticated understanding and nuanced analysis.

User Question: {refined_question}
{data_quality_note}

RESPONSE REQUIREMENTS:
- **Length**: {response_length.upper()} - {length_instruction}
- **Depth**: {response_depth.upper()} - {depth_instruction}

Document Summaries (in TOON format):
{summaries_toon_str}

Detailed Chunks (in TOON format):
{chunks_toon_str}{chunks_note}

INTELLIGENT REASONING PROCESS (Follow these steps internally before writing your answer):

1. **Comprehensive Understanding**: 
   - First, deeply understand what the user is really asking. Look beyond surface-level keywords.
   - Identify implicit questions, underlying concerns, or related aspects the user might want to know.
   - Consider what information would be most valuable and relevant to answer their question comprehensively.

2. **Critical Analysis**:
   - Analyze the retrieved information for credibility, relevance, and completeness.
   - Identify patterns, relationships, and connections between different pieces of information.
   - Recognize contradictions, gaps, or areas where information might be incomplete.
   - Evaluate the strength and reliability of the evidence provided.

3. **Intelligent Synthesis**:
   - Synthesize information from multiple sources to create a coherent, unified answer.
   - Prioritize the most relevant and important information while maintaining context.
   - Connect related concepts and present them in a logical, flowing manner.
   - Extract key insights, implications, and actionable takeaways when appropriate.

4. **Contextual Reasoning**:
   - Consider the broader context: What might the user be trying to accomplish?
   - Anticipate follow-up questions and address related aspects proactively.
   - Provide necessary background information to make the answer more meaningful.
   - Consider practical implications and real-world applications.

5. **Quality Assurance**:
   - Ensure your answer directly addresses the question (or explains why it cannot be fully answered).
   - Verify that claims are supported by the provided evidence.
   - Check for logical consistency and flow.
   - Confirm that the answer provides genuine value and insight.

CRITICAL FORMATTING AND CONTENT INSTRUCTIONS:

1. **Answer Structure & Conversational Tone**: Format your answer in a professional, friendly, and conversational manner with:
   - A clear, engaging introduction that addresses the question naturally (avoid robotic phrases like "Based on the provided information")
   - Well-organized sections with proper headings or bullet points where appropriate
   - Smooth transitions between topics that feel natural
   - A concise conclusion that summarizes key points (unless response_length is "brief", then skip conclusion)
   - Use natural language and avoid overly formal or stilted phrasing
   - If this is a follow-up question, acknowledge the conversation context naturally

2. **Document Citations**: 
   - ALWAYS mention the document titles when referencing information
   - Use professional citation formats such as:
     * "According to the [Document Title]..."
     * "As outlined in the [Document Title]..."
     * "The [Document Title] indicates that..."
     * "Per the [Document Title]..."
   - When referencing multiple documents, clearly distinguish between them
   - DO NOT include page numbers, chunk indices, or any location references
   - At the end of your answer, include a "References" or "Sources" section listing all documents cited

3. **Intelligent Content Synthesis**:
   - **Think First, Then Answer**: Before writing, mentally map out the key points, their relationships, and the most logical structure.
   - **Prioritize Information**: Lead with the most important and relevant information. Don't just list facts—explain their significance.
   - **Connect the Dots**: Show how different pieces of information relate to each other and to the user's question.
   - **Add Value**: Go beyond simply restating information—provide insights, implications, and actionable conclusions when relevant.
   - Use Document Summaries to understand overall context and themes
   - Use Detailed Chunks to extract specific facts, examples, and evidence
   - Answer using ONLY the information provided, but think critically about how to present it most effectively
   - Match the depth level: high-level for strategic overviews, moderate for balanced practical detail, deep for thorough analysis, exhaustive for comprehensive coverage
   - When information is limited, be transparent about this while still providing maximum value from available data

4. **Data Quality Awareness**:
   - Consider the data quality assessment: {data_quality} (confidence: {confidence_score}/100)
   - If data quality is "insufficient", provide the best answer possible but acknowledge limitations
   - If data quality is "excellent", provide comprehensive, detailed information
   - Be honest about information gaps if the retrieved chunks don't fully address the question

5. **Financial Data Restriction**:
   - DO NOT reveal any financial data, pricing information, costs, budgets, payment terms, or monetary values
   - If such information is requested, politely decline and explain that financial details are confidential

6. **Answer Intelligence & Quality**:
   - **Demonstrate Deep Understanding**: Show that you've truly comprehended the question and the available information.
   - **Provide Actionable Insights**: When appropriate, highlight key takeaways, implications, or next steps.
   - **Anticipate Follow-ups**: Address natural follow-up questions or related topics proactively.
   - **Show Reasoning**: Use phrases like "This suggests that...", "This indicates...", "Taken together, this means..." to show analytical thinking.
   - **Be Precise**: Use specific details, examples, and concrete information rather than vague generalizations.
   - **Maintain Intellectual Rigor**: Challenge assumptions when appropriate, acknowledge limitations, and present balanced perspectives.

7. **Organization & Structure**:
   - **Opening Insight**: Start with a clear, insightful statement that directly addresses the core of the question.
   - **Logical Progression**: Build your answer logically, with each section naturally leading to the next.
   - **Evidence-Based**: Support key points with specific information from the documents.
   - **Critical Analysis**: Don't just report—analyze, compare, contrast, and synthesize when relevant.
   - **Practical Relevance**: Connect information to real-world applications or implications when appropriate.
   - **Structure**: If multiple documents are referenced, organize clearly by topic or document. Use proper formatting (paragraphs, lists, sections) for readability.
   - **Synthesis**: Conclude with a thoughtful synthesis that ties everything together meaningfully (unless response_length is "brief").
   - **Completeness**: If information doesn't contain enough detail, say so clearly and explain what's available. Your answer should be self-contained with all document references included.

Format your answer as an intelligent, well-reasoned, and insightful response that demonstrates sophisticated understanding and provides genuine value. Write in a professional yet conversational tone that engages the reader while maintaining intellectual rigor.
{f'Note: This is a follow-up question in an ongoing conversation. Reference previous context naturally and provide a cohesive response that builds intelligently on earlier discussion.' if is_follow_up else ''}

Think through your answer carefully, then provide a response that showcases intelligent analysis and deep understanding:

Answer:"""

        # Track Step 4 prompt tokens
        step4_prompt_tokens = len(enc.encode(step4_prompt))
        
        # Build intelligent system message with enhanced reasoning capabilities
        system_message = "You are an expert AI assistant with advanced analytical and reasoning capabilities. Your primary strengths include: "
        system_message += "(1) Deep comprehension and critical analysis of complex information, "
        system_message += "(2) Intelligent synthesis that connects concepts and identifies patterns, "
        system_message += "(3) Contextual reasoning that considers implications and practical applications, "
        system_message += "(4) Clear communication that presents insights in an accessible yet sophisticated manner. "
        
        if is_follow_up:
            system_message += "This is a follow-up question in an ongoing conversation. Use your reasoning abilities to: "
            system_message += "(a) Understand how this question relates to previous discussion, "
            system_message += "(b) Synthesize new information with previous context intelligently, "
            system_message += "(c) Provide a cohesive response that demonstrates continuity of thought. "
        
        if is_clarification:
            system_message += "The user is asking for clarification or elaboration. Apply your analytical skills to: "
            system_message += "(a) Identify what aspect needs deeper explanation, "
            system_message += "(b) Break down complex concepts into clear, understandable components, "
            system_message += "(c) Provide examples and analogies that enhance understanding. "
        
        system_message += "Always cite document titles professionally (never page numbers). Structure responses intelligently with clear sections and a references list. "
        system_message += "Never reveal financial data, pricing, costs, budgets, or payment information. "
        system_message += "Think critically, reason deeply, and provide answers that demonstrate genuine intelligence and insight while remaining conversational and accessible."
        
        # Build messages array with conversation history
        messages = [
                {
                    "role": "system",
                "content": system_message
            }
        ]
        
        # Add conversation history (last 5 messages to avoid token bloat)
        if conversation_history:
            for msg in conversation_history[-5:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ["user", "assistant"] and content:
                    messages.append({
                        "role": role,
                        "content": content
                    })
        
        # Add current prompt
        messages.append({
            "role": "user",
            "content": step4_prompt
        })
        
        # Call OpenAI for final answer with dynamic settings optimized for intelligence
        # Adjust temperature based on query complexity for optimal reasoning
        if has_complex_question or is_clarification:
            reasoning_temperature = 0.5  # Lower temperature for more precise reasoning on complex queries
        elif is_follow_up:
            reasoning_temperature = 0.6  # Slightly lower for better context coherence
        else:
            reasoning_temperature = 0.7  # Standard temperature for conversational responses
        
        print(f"Using model: {selected_model} with max_tokens: {dynamic_max_tokens}, temperature: {reasoning_temperature} (conversational={is_follow_up}, complex={has_complex_question})")
        step4_response = client.chat.completions.create(
            model=selected_model,
            messages=messages,
            max_tokens=dynamic_max_tokens,
            temperature=reasoning_temperature  # Optimized for intelligent reasoning
        )
        
        # Track Step 4 response tokens
        step4_response_tokens = step4_response.usage.total_tokens if hasattr(step4_response, 'usage') else 0
        step4_total_json = step4_prompt_tokens + step4_response_tokens + step4_summaries_json + step4_chunks_json
        step4_total_toon = step4_prompt_tokens + step4_response_tokens + step4_summaries_toon + step4_chunks_toon
        step4_savings = step4_total_json - step4_total_toon
        step4_savings_percent = (step4_savings / step4_total_json * 100) if step4_total_json > 0 else 0
        
        # Track Step 4
        token_usage_tracker["calls"].append({
            "call_name": "Step 4: Final Answer Generation",
            "json_tokens": step4_total_json,
            "toon_tokens": step4_total_toon,
            "savings": step4_savings,
            "savings_percent": step4_savings_percent
        })
        
        # Extract answer
        answer = step4_response.choices[0].message.content.strip()
        
        # ====================================================================
        # STEP 4 DECISION: Evaluate answer quality and potentially refine
        # Based on data quality from Step 3 and answer completeness
        # ====================================================================
        data_quality = step3_decision.get("data_quality", "sufficient")
        confidence_score = step3_decision.get("confidence_score", 70)
        
        # If data quality was insufficient or confidence is low, add a disclaimer
        if data_quality == "insufficient" or (isinstance(confidence_score, (int, float)) and confidence_score < 60):
            print(f"Step 4 Quality Check: Data quality was {data_quality}, confidence: {confidence_score}")
            if "Note:" not in answer and "Disclaimer:" not in answer and "Please note" not in answer.lower():
                answer = f"{answer}\n\n*Note: The answer above is based on limited available information. For more comprehensive details, please rephrase your question or provide additional context.*"
        
        print(f"Step 4 Complete: Answer generated with {len(answer)} characters")
        print(f"  Data Quality: {data_quality}")
        print(f"  Confidence Score: {confidence_score}/100")
        
        # Store Step 4 response with complete information
        step4_response_data = {
            "call_name": "Step 4: Final Answer Generation",
            "request_prompt": step4_prompt,  # Store full prompt (will be truncated in JSON if needed)
            "response_content": answer,  # Store full answer (will be truncated in JSON if needed)
            "model_used": selected_model,
            "tokens_used": step4_total_toon,
            "tokens_without_toon": step4_total_json,
            "savings": step4_savings,
            "savings_percent": step4_savings_percent,
            "max_tokens": dynamic_max_tokens,
            "temperature": 0.7,
            "response_length": response_length,
            "response_depth": response_depth,
            "data_quality": step3_decision.get("data_quality", "sufficient"),
            "confidence_score": step3_decision.get("confidence_score", 70),
            "conversation_history_length": conversation_length,
            "is_follow_up": is_follow_up,
            "is_clarification": is_clarification,
            "model_selection_score": base_model_score if 'base_model_score' in locals() else 0,
            "dynamic_max_tokens_calculation": {
                "base_estimate": estimated_tokens,
                "model_adjustment": selected_model,
                "conversation_adjustment": is_follow_up,
                "response_length_adjustment": response_length,
                "final_max_tokens": dynamic_max_tokens
            }
        }
        api_call_responses.append(step4_response_data)
        
        # Calculate totals
        token_usage_tracker["total_json_tokens"] = sum(call["json_tokens"] for call in token_usage_tracker["calls"])
        token_usage_tracker["total_toon_tokens"] = sum(call["toon_tokens"] for call in token_usage_tracker["calls"])
        token_usage_tracker["total_savings"] = token_usage_tracker["total_json_tokens"] - token_usage_tracker["total_toon_tokens"]
        token_usage_tracker["total_savings_percent"] = (
            (token_usage_tracker["total_savings"] / token_usage_tracker["total_json_tokens"] * 100)
            if token_usage_tracker["total_json_tokens"] > 0 else 0.0
        )
        
        # Convert answer to Slack message format
        slack_formatted_answer = format_answer_for_slack(answer)
        
        # Prepare TOON savings breakdown
        toon_savings_breakdown = {
            "by_call": token_usage_tracker["calls"],
            "total_savings": token_usage_tracker["total_savings"],
            "total_savings_percent": round(token_usage_tracker["total_savings_percent"], 2)
        }
        
        # Prepare token usage summary (with optimization metadata)
        total_api_calls = len(token_usage_tracker["calls"])
        flow_type = "full_flow" if total_api_calls >= 3 else "early_exit"
        
        token_usage_summary = {
            "total_tokens_used": token_usage_tracker["total_toon_tokens"],
            "total_tokens_without_toon": token_usage_tracker["total_json_tokens"],
            "total_savings": token_usage_tracker["total_savings"],
            "total_savings_percent": round(token_usage_tracker["total_savings_percent"], 2),
            "total_api_calls": total_api_calls,
            "flow_optimization": flow_type,
            "optimized_from_6_calls": True,  # Indicates we're using the optimized 3-call flow
            "breakdown_by_call": [
                {
                    "call": call["call_name"],
                    "tokens_used": call["toon_tokens"],
                    "tokens_without_toon": call["json_tokens"],
                    "savings": call["savings"],
                    "savings_percent": round(call["savings_percent"], 2)
                }
                for call in token_usage_tracker["calls"]
            ]
        }
        
        # Log comprehensive token usage
        print(f"\n{'='*60}")
        print(f"TOKEN USAGE SUMMARY")
        print(f"{'='*60}")
        print(f"Total Tokens Used (TOON): {token_usage_tracker['total_toon_tokens']}")
        print(f"Total Tokens Without TOON: {token_usage_tracker['total_json_tokens']}")
        print(f"Total Savings: {token_usage_tracker['total_savings']} tokens ({token_usage_tracker['total_savings_percent']:.2f}%)")
        print(f"\nBreakdown by Call:")
        for call in token_usage_tracker["calls"]:
            print(f"  {call['call_name']}:")
            print(f"    TOON Tokens: {call['toon_tokens']}")
            print(f"    JSON Tokens: {call['json_tokens']}")
            print(f"    Savings: {call['savings']} tokens ({call['savings_percent']:.2f}%)")
        print(f"{'='*60}\n")
        
        # Calculate processing time
        end_time = time.time()
        processing_time = end_time - start_time
        
        # Log query with comprehensive data (if user context available, otherwise log as system query)
        # Note: ask_question endpoint may be called without authentication
        try:
            # Use system user (ID 1) if no user, or create a default system user
            # First check if system user exists
            system_user = db.query(User).filter(User.id == 1).first()
            if not system_user:
                # Create a system user for logging
                from app.core.security import get_password_hash
                system_user = User(
                    name="System",
                    email="system@askmojo.com",
                    password=get_password_hash("system"),  # Hashed password
                    role="system",
                    is_active=True
                )
                db.add(system_user)
                db.commit()
                db.refresh(system_user)
            
            # Get relevant documents if available (they should be defined earlier in the function)
            docs_to_log = []
            if 'relevant_documents' in locals() and relevant_documents:
                docs_to_log = relevant_documents
            
            # Prepare JSON strings for storage (truncate if too long for SQLite)
            # SQLite TEXT can store up to ~1GB, but we'll limit to 10MB per field for comprehensive logging
            max_json_length = 10000000  # 10MB - increased to store complete API call data
            
            # Store comprehensive JSON data with all information
            # 1. Token Usage JSON - Complete breakdown with all API calls
            token_usage_json_str = None
            if token_usage_summary:
                # Enhance token_usage_summary with additional metadata
                enhanced_token_usage = {
                    **token_usage_summary,
                    "slack_user_query": request.question,  # Original Slack user query
                    "slack_user_email": request.slack_user_email,  # Slack user email
                    "processing_time_seconds": processing_time,
                    "timestamp": datetime.utcnow().isoformat() if 'datetime' in globals() else None
                }
                token_usage_json_str = json.dumps(enhanced_token_usage, indent=2)
                if len(token_usage_json_str) > max_json_length:
                    token_usage_json_str = token_usage_json_str[:max_json_length] + "...[truncated]"
            
            # 2. API Calls JSON - Complete information for all OpenAI API calls
            # Each API call includes: full request prompt, full response, model, tokens, savings, etc.
            api_calls_json_str = None
            if api_call_responses:
                # Enhance API calls with additional context
                enhanced_api_calls = []
                for call in api_call_responses:
                    enhanced_call = {
                        **call,
                        "slack_user_query": request.question,  # Original query for context
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    enhanced_api_calls.append(enhanced_call)
                api_calls_json_str = json.dumps(enhanced_api_calls, indent=2)
                if len(api_calls_json_str) > max_json_length:
                    api_calls_json_str = api_calls_json_str[:max_json_length] + "...[truncated]"
            
            # 3. TOON Savings JSON - Complete TOON savings breakdown
            toon_savings_json_str = None
            if toon_savings_breakdown:
                # Enhance TOON savings with additional metadata
                enhanced_toon_savings = {
                    **toon_savings_breakdown,
                    "slack_user_query": request.question,  # Original query for context
                    "total_api_calls": total_api_calls_logged,
                    "flow_type": response_type,
                    "timestamp": datetime.utcnow().isoformat()
                }
                toon_savings_json_str = json.dumps(enhanced_toon_savings, indent=2)
                if len(toon_savings_json_str) > max_json_length:
                    toon_savings_json_str = toon_savings_json_str[:max_json_length] + "...[truncated]"
            
            # Truncate answer if too long (SQLite TEXT limit is large, but we'll limit to 10MB for performance)
            max_answer_length = 10000000  # 10MB
            stored_answer = answer
            if stored_answer and len(stored_answer) > max_answer_length:
                stored_answer = stored_answer[:max_answer_length] + "...[truncated]"
            
            # Determine response type and API call count based on flow
            response_type = token_usage_summary.get("flow_optimization", "full_flow") if token_usage_summary else "full_flow"
            total_api_calls_logged = token_usage_summary.get("total_api_calls", len(token_usage_tracker["calls"])) if token_usage_summary else len(token_usage_tracker["calls"])
            
            query_log = QueryLog(
                user_id=system_user.id,  # Use system user for public queries
                query=request.question,
                intent=None,  # Can be extracted from AI analysis if needed
                response_type=response_type,  # "full_flow", "early_exit_step1", "early_exit_step2"
                used_internal_only=False,  # Can be determined from document filtering
                answer=stored_answer,  # Store the original answer (not Slack formatted)
                processing_time_seconds=processing_time,
                total_tokens_used=token_usage_summary.get("total_tokens_used") if token_usage_summary else None,
                total_tokens_without_toon=token_usage_summary.get("total_tokens_without_toon") if token_usage_summary else None,
                token_savings=token_usage_summary.get("total_savings") if token_usage_summary else None,
                token_savings_percent=token_usage_summary.get("total_savings_percent") if token_usage_summary else None,
                token_usage_json=token_usage_json_str,
                api_calls_json=api_calls_json_str,
                toon_savings_json=toon_savings_json_str,
                slack_user_email=request.slack_user_email  # Store Slack user email if provided
            )
            db.add(query_log)
            db.commit()
            db.refresh(query_log)
            
            # Log sources if available
            if docs_to_log:
                from app.sqlite.models import QuerySource
                for doc in docs_to_log:
                    if isinstance(doc, dict) and "document_id" in doc:
                        source = QuerySource(
                            query_id=query_log.id,
                            document_id=doc["document_id"],
                            chunk_id=None,
                            relevance_score=doc.get("score", 0.0)
                        )
                        db.add(source)
                db.commit()
            
            print(f"✓ Query logged successfully (ID: {query_log.id}, Time: {processing_time:.2f}s, API Calls: {total_api_calls_logged}, Flow: {response_type}, Slack User: {request.slack_user_email or 'N/A'})")
        except Exception as e:
            # Don't fail the request if logging fails
            print(f"Error logging query: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # Return answer with token usage, savings data, and API call responses
        return AskResponse(
            answer=slack_formatted_answer,
            token_usage=token_usage_summary,
            toon_savings=toon_savings_breakdown,
            api_calls=api_call_responses
        )
        
    except Exception as e:
        print(f"Error in ask endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing question: {str(e)}"
        )
        print(f"Error in ask endpoint: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing question: {str(e)}"
        )
