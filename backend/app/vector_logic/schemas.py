from datetime import datetime
from pydantic import BaseModel


class DocumentBase(BaseModel):
    title: str
    category: str | None = None
    description: str | None = None
    source_type: str = "pdf"
    internal_only: bool = False


class DocumentCreate(DocumentBase):
    uploaded_by: int


class DocumentUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    description: str | None = None
    source_type: str | None = None
    internal_only: bool | None = None


class DocumentResponse(DocumentBase):
    id: int
    file_name: str | None
    file_path: str | None
    processed: bool
    uploaded_by: int
    created_at: datetime
    category_id: int | None = None
    domain_id: int | None = None

    class Config:
        from_attributes = True


class DocumentStatusResponse(BaseModel):
    id: int
    title: str
    processed: bool
    file_name: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class VectorQueryRequest(BaseModel):
    query: str
    category: str | None = None
    top_k: int = 5


class VectorQueryResult(BaseModel):
    id: str
    document: str
    score: float
    metadata: dict


class AskRequest(BaseModel):
    question: str
    slack_user_email: str | None = None  # Email of Slack user (if query comes from Slack)
    conversation_history: list[dict] | None = None  # Previous conversation messages for context
    max_tokens: int | None = None  # Optional: Override max tokens (will be dynamically calculated if not provided)
    model_preference: str | None = None  # Optional: "gpt-4o", "gpt-4o-mini", or None for auto-selection
    # AI model will automatically determine optimal top_k_documents and top_k_chunks_per_document


class AIDecisionResponse(BaseModel):
    question: str  # Refined/extracted question
    top_k_documents: int
    top_k_chunks_per_document: int
    reasoning: str  # Why these parameters were chosen


class SourceChunk(BaseModel):
    document_id: int        # Which document did this come from? (e.g., ID 42)
    document_title: str     # Readable name (e.g., "Project Proposal.pdf")
    category: str | None    # Context (e.g., "Legal" or "HR")
    chunk_text: str         # The actual content/paragraph used as evidence.
    page_number: int | None # Where to find it (e.g., "Page 5")
    chunk_index: int | None # Technical index (e.g., "Chunk #12 of the file")
    score: float            # Similarity Score: How relevant is this chunk? 
                            # (Lower distance usually means better match in ChromaDB)

class TokenUsage(BaseModel):
    """Token usage information for a single API call"""
    call_name: str
    json_tokens: int
    toon_tokens: int
    savings: int
    savings_percent: float


class APICallResponse(BaseModel):
    """Response data for a single API call"""
    call_name: str
    request_prompt: str | None = None
    response_content: str | dict | None = None
    model_used: str | None = None
    tokens_used: int | None = None
    tokens_without_toon: int | None = None
    savings: int | None = None
    savings_percent: float | None = None


class AskResponse(BaseModel):
    answer: str
    token_usage: dict | None = None  # Total token usage summary
    toon_savings: dict | None = None  # TOON savings breakdown
    api_calls: list[APICallResponse] | None = None  # Detailed response for each API call
    followups: list[dict] | None = None  # Suggested follow-up prompts (text + type)
    sources: list[str] | None = None  # List of source document titles used for the answer

