from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Dict, Any

from app.sqlite.database import get_db
from app.sqlite.models import User, Document, QueryLog, Category, DocumentUploadLog, QuerySource
from app.core.security import get_current_admin_user
from app.admin.schemas import (
    AdminUserCreate, AdminUserUpdate, AdminUserResponse, AdminStatsResponse,
    CategoryCreate, CategoryUpdate, CategoryResponse, DomainResponse,
    QueryLogResponse, DocumentUploadLogResponse,
    CategoryDomainBackfillRequest, CategoryDomainBackfillResponse
)
from sqlalchemy.orm import joinedload
from app.core.security import get_password_hash
from sqlalchemy import func

# Concurrency management
from app.pdf_extraction.concurrency_manager import ConcurrencyManager

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/stats", response_model=AdminStatsResponse)
def get_admin_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get admin dashboard statistics.
    """
    total_users = db.query(func.count(User.id)).scalar() or 0
    total_documents = db.query(func.count(Document.id)).scalar() or 0
    total_queries = db.query(func.count(QueryLog.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0
    admin_users = db.query(func.count(User.id)).filter(User.role == "admin").scalar() or 0
    
    return AdminStatsResponse(
        total_users=total_users,
        total_documents=total_documents,
        total_queries=total_queries,
        active_users=active_users,
        admin_users=admin_users
    )


@router.get("/processing/concurrency")
def get_processing_concurrency_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Get document processing concurrency statistics for current admin.
    
    Shows:
    - Current concurrent processing count (max 15 per admin)
    - Queue length (documents waiting to be processed)
    - Remaining capacity
    - Utilization percentage
    """
    stats = ConcurrencyManager.get_stats(current_user.id, db)
    
    return {
        "admin_id": stats["admin_id"],
        "admin_name": current_user.name,
        "concurrent_processing": stats["concurrent_processing"],
        "queue_length": stats["queue_length"],
        "remaining_capacity": stats["remaining_capacity"],
        "max_capacity": stats["max_capacity"],
        "utilization_percent": round(stats["utilization_percent"], 1),
        "status": "At capacity" if stats["remaining_capacity"] == 0 else "Available",
    }


@router.post("/users", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: AdminUserCreate,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Create a new user (admin only).
    """
    # Check if email already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Validate role
    if user_data.role not in ["user", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be 'user' or 'admin'"
        )
    
    # Create new user with hashed password
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        name=user_data.name,
        email=user_data.email,
        password=hashed_password,
        role=user_data.role,
        is_active=user_data.is_active
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/users", response_model=list[AdminUserResponse])
def get_all_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get all users (admin only).
    """
    users = db.query(User).offset(skip).limit(limit).all()
    return users


@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific user by ID (admin only).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    return user


@router.put("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    user_update: AdminUserUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Update a user by ID (admin only).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    
    # Prevent admin from removing their own admin role
    if user_id == current_user.id and user_update.role and user_update.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin role"
        )
    
    # Check if email is being updated and if it already exists
    if user_update.email and user_update.email != user.email:
        existing_user = db.query(User).filter(User.email == user_update.email).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
    
    # Update only provided fields
    update_data = user_update.model_dump(exclude_unset=True, exclude={"password"})
    for field, value in update_data.items():
        setattr(user, field, value)
    
    # Handle password update separately (hash it)
    if user_update.password:
        user.password = get_password_hash(user_update.password)
    
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a user by ID (admin only).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    
    # Prevent admin from deleting themselves
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    db.delete(user)
    db.commit()
    return None


@router.post("/users/{user_id}/toggle-active", response_model=AdminUserResponse)
def toggle_user_active(
    user_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Toggle user active status (admin only).
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} not found"
        )
    
    # Prevent admin from deactivating themselves
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )
    
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return user


# Category Management Routes
@router.get("/categories", response_model=list[CategoryResponse])
def get_all_categories(
    skip: int = 0,
    limit: int = 100,
    domain_id: int | None = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get all categories (admin only).
    """
    from app.sqlite.models import Domain
    
    query = db.query(Category)
    if domain_id:
        # Filter categories that have the specified domain
        query = query.join(Category.domains).filter(Domain.id == domain_id)
    
    categories = query.offset(skip).limit(limit).all()
    result = []
    for category in categories:
        # Count documents in this category
        doc_count = db.query(func.count(Document.id)).filter(
            Document.category_id == category.id
        ).scalar() or 0
        
        # Convert domains to response format
        domain_responses = [DomainResponse.from_orm(d) for d in category.domains] if category.domains else []
        
        category_dict = {
            "id": category.id,
            "name": category.name,
            "description": category.description,
            "domains": domain_responses,
            "collection_name": category.collection_name,
            "is_active": category.is_active,
            "created_at": category.created_at,
            "updated_at": category.updated_at,
            "document_count": doc_count
        }
        result.append(CategoryResponse(**category_dict))
    return result


@router.get("/categories/{category_id}", response_model=CategoryResponse)
def get_category(
    category_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific category by ID (admin only).
    """
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {category_id} not found"
        )
    
    # Count documents in this category
    doc_count = db.query(func.count(Document.id)).filter(
        Document.category_id == category.id
    ).scalar() or 0
    
    # Convert domains to response format
    domain_responses = [DomainResponse.from_orm(d) for d in category.domains] if category.domains else []
    
    return CategoryResponse(
        id=category.id,
        name=category.name,
        description=category.description,
        domains=domain_responses,
        collection_name=category.collection_name,
        is_active=category.is_active,
        created_at=category.created_at,
        updated_at=category.updated_at,
        document_count=doc_count
    )


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(
    category_data: CategoryCreate,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Create a new category (admin only).
    Also creates the corresponding ChromaDB collection.
    """
    # Check if category name already exists
    existing_category = db.query(Category).filter(Category.name == category_data.name).first()
    if existing_category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Category name already exists"
        )
    
    # Generate collection name from category name (normalize)
    collection_name = category_data.name.lower().replace(" ", "_").replace("-", "_")
    # Remove special characters
    collection_name = "".join(c for c in collection_name if c.isalnum() or c == "_")
    
    # Check if collection name already exists
    existing_collection = db.query(Category).filter(Category.collection_name == collection_name).first()
    if existing_collection:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Collection name '{collection_name}' already exists"
        )
    
    # Ensure ChromaDB collection exists (create if it doesn't)
    # Pass category description to be stored in ChromaDB collection metadata
    from app.vector_logic.vector_store import ensure_collection_exists
    if not ensure_collection_exists(collection_name, category_description=category_data.description):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create ChromaDB collection '{collection_name}'"
        )
    
    # Create new category
    new_category = Category(
        name=category_data.name,
        description=category_data.description,
        collection_name=collection_name,
        is_active=category_data.is_active
    )
    db.add(new_category)
    db.flush()  # Get the category ID
    
    # Add domains if provided
    if category_data.domain_ids:
        from app.sqlite.models import Domain
        domains = db.query(Domain).filter(Domain.id.in_(category_data.domain_ids)).all()
        new_category.domains = domains
    
    db.commit()
    db.refresh(new_category)
    
    # Convert domains to response format
    domain_responses = [DomainResponse.from_orm(d) for d in new_category.domains] if new_category.domains else []
    
    return CategoryResponse(
        id=new_category.id,
        name=new_category.name,
        description=new_category.description,
        domains=domain_responses,
        collection_name=new_category.collection_name,
        is_active=new_category.is_active,
        created_at=new_category.created_at,
        updated_at=new_category.updated_at,
        document_count=0
    )


@router.put("/categories/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    category_update: CategoryUpdate,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Update a category by ID (admin only).
    If category name changes, the ChromaDB collection is also renamed.
    """
    from app.vector_logic.vector_store import rename_chromadb_collection, ensure_collection_exists
    
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {category_id} not found"
        )
    
    old_collection_name = category.collection_name
    new_collection_name = old_collection_name  # Default to same name
    collection_name_changed = False
    
    # Check if name is being updated and if it already exists
    if category_update.name and category_update.name != category.name:
        existing_category = db.query(Category).filter(Category.name == category_update.name).first()
        if existing_category:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category name already exists"
            )
        # Update collection name if name changed
        new_collection_name = category_update.name.lower().replace(" ", "_").replace("-", "_")
        new_collection_name = "".join(c for c in new_collection_name if c.isalnum() or c == "_")
        
        # Check if new collection name already exists
        existing_collection = db.query(Category).filter(
            Category.collection_name == new_collection_name,
            Category.id != category_id
        ).first()
        if existing_collection:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Collection name '{new_collection_name}' already exists"
            )
        
        # Check if collection name actually changed
        if old_collection_name != new_collection_name:
            collection_name_changed = True
            # Rename ChromaDB collection
            # Get the new description (from update or keep existing)
            new_description = category_update.description if category_update.description is not None else category.description
            if not rename_chromadb_collection(old_collection_name, new_collection_name, category_description=new_description):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to rename ChromaDB collection from '{old_collection_name}' to '{new_collection_name}'"
                )
            category.collection_name = new_collection_name
    
    # Handle description update when collection name doesn't change
    # (or when name is not being updated at all)
    if not collection_name_changed:
        # Collection name didn't change, but description might have
        # Update collection metadata if description changed
        if category_update.description is not None and category_update.description != category.description:
            # Update ChromaDB collection metadata by recreating it
            from app.vector_logic.vector_store import update_collection_metadata
            if not update_collection_metadata(old_collection_name, category_description=category_update.description):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to update ChromaDB collection metadata for '{old_collection_name}'"
                )
            print(f"Updated ChromaDB collection metadata for '{category.name}' with new description")
    
    # Update only provided fields (excluding domain_ids)
    update_data = category_update.model_dump(exclude_unset=True, exclude={"name", "domain_ids"})
    for field, value in update_data.items():
        setattr(category, field, value)
    
    # Handle name update separately (already handled above)
    if category_update.name:
        category.name = category_update.name
    
    # Handle domain_ids update
    if category_update.domain_ids is not None:
        from app.sqlite.models import Domain
        domains = db.query(Domain).filter(Domain.id.in_(category_update.domain_ids)).all()
        category.domains = domains
    
    db.commit()
    db.refresh(category)
    
    # Count documents
    doc_count = db.query(func.count(Document.id)).filter(
        Document.category_id == category.id
    ).scalar() or 0
    
    # Convert domains to response format
    domain_responses = [DomainResponse.from_orm(d) for d in category.domains] if category.domains else []
    
    return CategoryResponse(
        id=category.id,
        name=category.name,
        description=category.description,
        domains=domain_responses,
        collection_name=category.collection_name,
        is_active=category.is_active,
        created_at=category.created_at,
        updated_at=category.updated_at,
        document_count=doc_count
    )


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a category by ID (admin only).
    Also deletes the corresponding ChromaDB collection if it exists and is empty.
    """
    from app.vector_logic.vector_store import _get_chroma_client, _get_persist_directory
    
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {category_id} not found"
        )
    
    # Check if category has documents
    doc_count = db.query(func.count(Document.id)).filter(
        Document.category_id == category.id
    ).scalar() or 0
    
    if doc_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete category with {doc_count} document(s). Please reassign or delete documents first."
        )
    
    collection_name = category.collection_name
    
    # Delete from SQLite first
    db.delete(category)
    db.commit()
    
    # Try to delete ChromaDB collection if it exists and is empty
    try:
        persist_directory = _get_persist_directory(None)
        client = _get_chroma_client(persist_directory)
        
        try:
            collection = client.get_collection(name=collection_name)
            # Check if collection is empty
            count = collection.count()
            if count == 0:
                client.delete_collection(name=collection_name)
                print(f"Deleted empty ChromaDB collection: {collection_name}")
            else:
                print(f"Warning: ChromaDB collection '{collection_name}' has {count} items, not deleting")
        except Exception as e:
            # Collection doesn't exist or error accessing it, which is fine
            print(f"Info: Could not delete ChromaDB collection '{collection_name}': {e}")
    except Exception as e:
        print(f"Warning: Error accessing ChromaDB during category deletion: {e}")
        # Don't fail the deletion if ChromaDB operation fails
    
    return None


    # ============================================================
# GENERATE AI DESCRIPTION FOR CATEGORY
# Analyzes all documents in category to create searchable description
# ============================================================

@router.post("/categories/{category_id}/generate-description")
def generate_category_ai_description(
    category_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Generate AI description for a category by analyzing all its documents.
    Uses Few-Shot + Structured Output for optimal search matching.
    """
    # Import the category description generator
    from app.vector_logic.description_generator import generate_category_description
    from app.vector_logic.vector_store import update_collection_metadata
    
    # Step 1: Get category from database
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category with id {category_id} not found"
        )
    
    # Step 2: Get all documents in this category
    documents = db.query(Document).filter(Document.category_id == category_id).all()
    
    if not documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No documents in this category to analyze"
        )
    
    # Step 3: Collect all document descriptions
    doc_summaries = []
    for doc in documents:
        if doc.description:
            doc_summaries.append(f"Document: {doc.title}\n{doc.description}")
    
    if not doc_summaries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No document descriptions available. Upload and process documents first."
        )
    
    # Step 4: Generate category description using AI
    try:
        combined_content = "\n\n---\n\n".join(doc_summaries)
        new_description = generate_category_description(
            category_name=category.name,
            document_summaries=combined_content
        )
        
        # Step 5: Update category description in database
        category.description = new_description
        db.commit()
        db.refresh(category)
        
        # Step 6: Update ChromaDB collection metadata
        update_collection_metadata(category.collection_name, category_description=new_description)
        
        return {
            "success": True,
            "category_id": category_id,
            "category_name": category.name,
            "new_description": new_description,
            "documents_analyzed": len(doc_summaries)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate description: {str(e)}"
        )


# Logs Management Routes
@router.get("/logs/queries", response_model=list[QueryLogResponse])
def get_query_logs(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get query logs (admin only).
    """
    query_logs = db.query(QueryLog).options(
        joinedload(QueryLog.user)
    ).order_by(QueryLog.created_at.desc()).offset(skip).limit(limit).all()
    
    result = []
    for log in query_logs:
        # Count sources
        source_count = db.query(func.count(QuerySource.id)).filter(
            QuerySource.query_id == log.id
        ).scalar() or 0
        
        result.append(QueryLogResponse(
            id=log.id,
            user_id=log.user_id,
            user_name=log.user.name if log.user else None,
            user_email=log.user.email if log.user else None,
            query=log.query,
            intent=log.intent,
            response_type=log.response_type,
            used_internal_only=log.used_internal_only,
            created_at=log.created_at,
            source_count=source_count,
            answer=log.answer,
            processing_time_seconds=log.processing_time_seconds,
            total_tokens_used=log.total_tokens_used,
            total_tokens_without_toon=log.total_tokens_without_toon,
            token_savings=log.token_savings,
            token_savings_percent=log.token_savings_percent,
            token_usage_json=log.token_usage_json,
            api_calls_json=log.api_calls_json,
            toon_savings_json=log.toon_savings_json,
            slack_user_email=log.slack_user_email
        ))
    
    return result


# Domain Management Routes  
@router.get("/domains", response_model=list[DomainResponse])
def get_all_domains(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get all domains (admin only).
    """
    from app.sqlite.models import Domain
    domains = db.query(Domain).offset(skip).limit(limit).all()
    return domains


@router.post("/domains", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
def create_domain(
    domain_data: dict,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Create a new domain (admin only).
    """
    from app.sqlite.models import Domain
    
    # Check if domain name already exists
    existing_domain = db.query(Domain).filter(Domain.name == domain_data.get("name")).first()
    if existing_domain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Domain name already exists"
        )
    
    # Create new domain
    new_domain = Domain(
        name=domain_data.get("name").strip(),
        description=domain_data.get("description"),
        is_active=domain_data.get("is_active", True)
    )
    db.add(new_domain)
    db.commit()
    db.refresh(new_domain)
    
    return new_domain


@router.delete("/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_domain(
    domain_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """
    Delete a domain by ID (admin only).
    Cascades: deletes associated documents, upload logs, and cleans metadata.
    """
    from app.sqlite.models import Domain, Document, DocumentUploadLog
    domain = db.query(Domain).filter(Domain.id == domain_id).first()
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Domain with id {domain_id} not found"
        )
    # Delete associated documents and upload logs
    docs = db.query(Document).filter(Document.domain_id == domain_id).all()
    for doc in docs:
        db.query(DocumentUploadLog).filter(DocumentUploadLog.document_id == doc.id).delete()
        db.delete(doc)
    db.delete(domain)
    db.commit()
    return None

