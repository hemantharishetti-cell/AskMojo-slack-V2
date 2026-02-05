from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional


class AdminUserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "user"
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None


class AdminUserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminStatsResponse(BaseModel):
    total_users: int
    total_documents: int
    total_queries: int
    active_users: int
    admin_users: int


class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    is_active: bool = True


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CategoryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    collection_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    document_count: Optional[int] = 0  # Number of documents in this category

    class Config:
        from_attributes = True


class QueryLogResponse(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    query: str
    intent: Optional[str] = None
    response_type: Optional[str] = None
    used_internal_only: bool
    created_at: datetime
    source_count: Optional[int] = 0  # Number of sources used
    # Comprehensive logging fields
    answer: Optional[str] = None
    processing_time_seconds: Optional[float] = None
    total_tokens_used: Optional[int] = None
    total_tokens_without_toon: Optional[int] = None
    token_savings: Optional[int] = None
    token_savings_percent: Optional[float] = None
    token_usage_json: Optional[str] = None  # JSON string of token usage breakdown
    api_calls_json: Optional[str] = None  # JSON string of all API calls made
    toon_savings_json: Optional[str] = None  # JSON string of TOON savings breakdown
    slack_user_email: Optional[str] = None  # Email of Slack user who asked the question

    class Config:
        from_attributes = True


class DocumentUploadLogResponse(BaseModel):
    id: int
    document_id: int
    document_title: Optional[str] = None
    uploaded_by: int
    uploader_name: Optional[str] = None
    uploader_email: Optional[str] = None
    title: str
    file_name: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    category: Optional[str] = None
    description_generated: bool
    description_length: Optional[int] = None
    processing_started: bool
    processing_completed: bool
    processing_error: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None
    # Time and token usage tracking
    upload_time_seconds: Optional[float] = None
    description_generation_time_seconds: Optional[float] = None
    description_tokens_used: Optional[int] = None
    description_tokens_prompt: Optional[int] = None
    description_tokens_completion: Optional[int] = None

    class Config:
        from_attributes = True

