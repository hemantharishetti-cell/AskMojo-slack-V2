from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
)
from sqlalchemy.orm import relationship

from app.sqlite.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)  # Hashed password
    role = Column(String, nullable=False, default="user")  # user, admin
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    documents = relationship("Document", back_populates="uploader", cascade="all, delete-orphan")
    query_logs = relationship("QueryLog", back_populates="user", cascade="all, delete-orphan")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False, index=True)
    category = Column(String, nullable=True)  # Legacy: kept for backward compatibility
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)  # Foreign key to categories table
    description = Column(String, nullable=True)
    source_type = Column(String, nullable=False)
    internal_only = Column(Boolean, default=False, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    file_name = Column(String, nullable=True)  # Original filename
    file_path = Column(String, nullable=True)  # Stored file path
    processed = Column(Boolean, default=False, nullable=False)  # Whether vector processing is complete

    # Relationships
    uploader = relationship("User", back_populates="documents")
    category_ref = relationship("Category", back_populates="documents")
    versions = relationship("DocumentVersion", back_populates="document", cascade="all, delete-orphan")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    query_sources = relationship("QuerySource", back_populates="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    version = Column(Integer, nullable=False)
    file_path = Column(String, nullable=False)
    checksum = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    document = relationship("Document", back_populates="versions")
    chunks = relationship("DocumentChunk", back_populates="document_version", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    version_id = Column(Integer, ForeignKey("document_versions.id"), nullable=False)
    version = Column(Integer, nullable=False)  # Version number (denormalized for quick access)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    section = Column(String, nullable=True)

    # Relationships
    document = relationship("Document", back_populates="chunks")
    document_version = relationship("DocumentVersion", back_populates="chunks")  # Renamed from 'version' to avoid conflict with column
    query_sources = relationship("QuerySource", back_populates="chunk")


class QueryLog(Base):
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    query = Column(String, nullable=False)
    intent = Column(String, nullable=True)
    response_type = Column(String, nullable=True)
    used_internal_only = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Comprehensive logging fields
    answer = Column(String, nullable=True)  # The generated answer
    processing_time_seconds = Column(Float, nullable=True)  # Time taken to process the request
    total_tokens_used = Column(Integer, nullable=True)  # Total tokens used (with TOON)
    total_tokens_without_toon = Column(Integer, nullable=True)  # Total tokens without TOON
    token_savings = Column(Integer, nullable=True)  # Token savings from TOON
    token_savings_percent = Column(Float, nullable=True)  # Percentage savings
    token_usage_json = Column(String, nullable=True)  # JSON string of token usage breakdown
    api_calls_json = Column(String, nullable=True)  # JSON string of all API calls made
    toon_savings_json = Column(String, nullable=True)  # JSON string of TOON savings breakdown
    slack_user_email = Column(String, nullable=True)  # Email of Slack user who asked the question

    # Relationships
    user = relationship("User", back_populates="query_logs")
    sources = relationship("QuerySource", back_populates="query_log", cascade="all, delete-orphan")


class QuerySource(Base):
    __tablename__ = "query_sources"

    id = Column(Integer, primary_key=True, index=True)
    query_id = Column(Integer, ForeignKey("query_logs.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    chunk_id = Column(Integer, ForeignKey("document_chunks.id"), nullable=True)
    relevance_score = Column(Float, nullable=True)

    # Relationships
    query_log = relationship("QueryLog", back_populates="sources")
    document = relationship("Document", back_populates="query_sources")
    chunk = relationship("DocumentChunk", back_populates="query_sources")


class DocumentUploadLog(Base):
    __tablename__ = "document_upload_logs"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    file_name = Column(String, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    category = Column(String, nullable=True)  # Legacy category name
    description_generated = Column(Boolean, default=True, nullable=False)  # Whether description was auto-generated
    description_length = Column(Integer, nullable=True)  # Length of generated description
    processing_started = Column(Boolean, default=False, nullable=False)
    processing_completed = Column(Boolean, default=False, nullable=False)
    processing_error = Column(String, nullable=True)  # Error message if processing failed
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)  # When processing completed
    
    # Time and token usage tracking
    upload_time_seconds = Column(Float, nullable=True)  # Time taken for upload and description generation
    description_generation_time_seconds = Column(Float, nullable=True)  # Time taken specifically for description generation
    description_tokens_used = Column(Integer, nullable=True)  # Tokens used for description generation
    description_tokens_prompt = Column(Integer, nullable=True)  # Prompt tokens for description
    description_tokens_completion = Column(Integer, nullable=True)  # Completion tokens for description

    # Relationships
    document = relationship("Document")
    uploader = relationship("User")
    category_ref = relationship("Category")


class SlackIntegration(Base):
    __tablename__ = "slack_integrations"

    id = Column(Integer, primary_key=True, index=True)
    workspace_name = Column(String, nullable=True)
    workspace_id = Column(String, nullable=True)
    bot_token = Column(String, nullable=True)  # Bot User OAuth Token
    app_token = Column(String, nullable=True)  # App-Level Token for Socket Mode (starts with xapp-)
    socket_mode_enabled = Column(Boolean, default=False, nullable=False)  # Whether Socket Mode is enabled
    webhook_url = Column(String, nullable=True)  # Incoming Webhook URL (alternative to bot token, not used with Socket Mode)
    signing_secret = Column(String, nullable=True)  # For verifying requests from Slack (not needed with Socket Mode)
    channel_id = Column(String, nullable=True)  # Default channel to respond to
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SlackUser(Base):
    __tablename__ = "slack_users"

    id = Column(Integer, primary_key=True, index=True)
    slack_user_id = Column(String, unique=True, nullable=False, index=True)  # Slack user ID (U123456)
    email = Column(String, unique=True, nullable=False, index=True)  # User email
    name = Column(String, nullable=False)  # Display name
    real_name = Column(String, nullable=True)  # Real name
    display_name = Column(String, nullable=True)  # Display name
    image_24 = Column(String, nullable=True)  # Profile image 24x24
    image_32 = Column(String, nullable=True)  # Profile image 32x32
    image_48 = Column(String, nullable=True)  # Profile image 48x48
    image_72 = Column(String, nullable=True)  # Profile image 72x72
    image_192 = Column(String, nullable=True)  # Profile image 192x192
    is_admin = Column(Boolean, default=False, nullable=False)  # Is workspace admin
    is_owner = Column(Boolean, default=False, nullable=False)  # Is workspace owner
    is_bot = Column(Boolean, default=False, nullable=False)  # Is bot user
    is_active = Column(Boolean, default=True, nullable=False)  # Is user active in workspace
    timezone = Column(String, nullable=True)  # User timezone
    tz_label = Column(String, nullable=True)  # Timezone label
    tz_offset = Column(Integer, nullable=True)  # Timezone offset
    is_registered = Column(Boolean, default=True, nullable=False)  # Can use the Slack app
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)  # Category name (e.g., "Proposals", "Contracts")
    description = Column(String, nullable=True)  # Category description
    collection_name = Column(String, unique=True, nullable=False, index=True)  # ChromaDB collection name (normalized from name)
    is_active = Column(Boolean, default=True, nullable=False)  # Whether category is active
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    documents = relationship("Document", back_populates="category_ref")
