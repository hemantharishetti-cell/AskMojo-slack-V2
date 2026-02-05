from pydantic_settings import BaseSettings
import multiprocessing


class Settings(BaseSettings):
    app_name: str = "ASKMOJO Backend"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    # SQLite database URL, default stored under app/sqlite/app.db
    database_url: str = "sqlite:///./app/sqlite/app.db"
    # DB connection pool settings (optimized for multiprocessing)
    pool_size: int = max(5, multiprocessing.cpu_count())  # Scale with CPU cores
    max_overflow: int = 20  # Increased for better concurrency
    pool_pre_ping: bool = True
    pool_recycle: int = 3600  # Recycle connections after 1 hour
    pool_timeout: int = 30  # Timeout for getting connection from pool
    # SQLite-specific settings for multiprocessing
    sqlite_timeout: int = 20  # SQLite connection timeout in seconds
    sqlite_check_same_thread: bool = False  # Allow connections from different threads
    # Vector processing delay (in seconds)
    vector_processing_delay: int = 5
    # OpenAI API key for description generation
    openai_api_key: str | None = None
    # JWT Authentication settings
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 hours for better UX
    # ChromaDB settings
    chromadb_persist_directory: str | None = None  # Auto-detected if None
    # Multiprocessing settings
    max_workers: int = max(1, multiprocessing.cpu_count() - 1)  # For embedding generation
    # Tokenizers parallelism setting (for huggingface tokenizers)
    tokenizers_parallelism: str | None = None  # Set to "true" or "false" to control tokenizers parallelism

    class Config:
        env_file = ".env"
        extra = "ignore"  # Ignore extra environment variables that aren't in the Settings class


settings = Settings()

