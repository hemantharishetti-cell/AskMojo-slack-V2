from __future__ import annotations
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import multiprocessing
import os

# Load .env file explicitly
load_dotenv(dotenv_path=".env", override=True)


class Settings(BaseSettings):
    app_name: str = "ASKMOJO Backend"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    # SQLite database URL, default stored under app/sqlite/app.db
    database_url: str = "sqlite:///./app/sqlite/app.db"
    # DB connection pool settings (tuned for 2-vCPU Linux server)
    pool_size: int = 3           # 3 is sufficient on 2 vCPU; cpu_count() over-provisions
    max_overflow: int = 7        # max 10 total connections — safe for SQLite WAL mode
    pool_pre_ping: bool = True
    pool_recycle: int = 1800     # Recycle every 30 min (was 60 — frees stale handles faster)
    pool_timeout: int = 30       # Timeout for getting connection from pool
    # SQLite-specific settings for multiprocessing
    sqlite_timeout: int = 20  # SQLite connection timeout in seconds
    sqlite_check_same_thread: bool = False  # Allow connections from different threads
    # Vector processing delay (in seconds)
    vector_processing_delay: int = 5
    # OpenAI API key for description generation
    openai_api_key: str | None = None
    # CORS — comma-separated list of allowed origins (read from CORS_ORIGINS env var)
    # Example in .env:  CORS_ORIGINS=https://app.askmojo.io,https://admin.askmojo.io
    # In development leave as ["*"]; in production MUST be locked to your domain.
    cors_origins: List[str] = ["*"]

    # JWT Authentication settings
    # PRODUCTION: set SECRET_KEY to a 64-char random hex string in .env
    # Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours for better UX
    # ChromaDB settings
    chromadb_persist_directory: str | None = None  # Auto-detected if None
    # Retrieval tuning: relevance distance threshold (higher = more tolerant)
    relevance_threshold_distance: float = 1.1
    # Multiprocessing settings
    max_workers: int = max(1, multiprocessing.cpu_count() - 1)  # For embedding generation
    # Tokenizers parallelism setting (for huggingface tokenizers)
    tokenizers_parallelism: str | None = None  # Set to "true" or "false" to control tokenizers parallelism

    # Chunking / OpenAI safety settings
    model_context_limit: int = 128000  # Target model context window (tokens), e.g., GPT-4o (128k)
    openai_tpm_limit: int = 90000  # OpenAI tokens-per-minute safety limit (configurable)
    openai_rpm_limit: int = 60  # OpenAI requests-per-minute (informational)
    expected_requests_per_minute: int = 10  # Expected concurrent requests per minute for budgeting
    chunk_safety_buffer: float = 0.8  # Safety multiplier for chunk token budget
    chunk_max_tokens_hint: int | None = None  # Optional override for max tokens per chunk
    chunk_max_words_hint: int | None = None  # Optional override for max words per chunk
    target_top_k_for_budget: int = 6  # Default top_k used when computing per-chunk budgets
    
    # ── Adobe PDF Services API Settings ──────────────────────────────────
    adobe_api_key: str | None = None  # Adobe API key from Developer Console
    adobe_client_id: str | None = None  # OAuth2 Server-to-Server Client ID
    adobe_client_secret: str | None = None  # OAuth2 Server-to-Server Client Secret
    adobe_org_id: str | None = None  # Adobe Organization ID
    adobe_api_endpoint: str = "https://pdf-services.adobe.io/operation"  # Adobe API endpoint
    adobe_ims_endpoint: str = "https://ims-na1.adobelogin.com/ims/token/v3"  # IMS token endpoint
    adobe_monthly_limit: int = 500  # Free-tier limit
    adobe_fallback_to_pdfplumber: bool = True  # Fallback to pdfplumber if Adobe fails
    adobe_cache_extraction_results: bool = True  # Cache extraction results
    adobe_cache_expiry_days: int = 180  # Cache expiry (6 months)
    adobe_extraction_timeout_seconds: int = 300  # 5 minutes for extraction job
    adobe_polling_interval_seconds: int = 2  # Poll Adobe for job status every 2 seconds
    adobe_polling_max_retries: int = 150  # Max polls (150 * 2s = 5min timeout)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Ignore extra environment variables that aren't in the Settings class
        case_sensitive=False,  # Environment variables are case-insensitive
    )


settings = Settings()

# ── Production safety guard ───────────────────────────────────────────────────
# Crash fast on startup if the secret key has never been changed.
# This prevents silent auth bypass in production.
if settings.environment == "production" and settings.secret_key == "your-secret-key-change-in-production":
    raise RuntimeError(
        "[FATAL] SECRET_KEY is still the insecure default. "
        "Set a real key in .env:  SECRET_KEY=<64-char hex string>\n"
        "Generate with:  python -c \"import secrets; print(secrets.token_hex(32))\""
    )
