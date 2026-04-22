from sqlalchemy import create_engine, event, pool, Engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool
import threading
from contextlib import contextmanager

from app.core.config import settings


class Base(DeclarativeBase):
    """Base class for all ORM models."""


# SQLite connection arguments optimized for multiprocessing
connect_args = {
    "check_same_thread": settings.sqlite_check_same_thread,
    "timeout": settings.sqlite_timeout,
}

# Enable WAL (Write-Ahead Logging) mode for better concurrency
# This allows multiple readers and one writer simultaneously
# Listen to the Engine class, not the create_engine function
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode and optimize SQLite for concurrent access."""
    cursor = dbapi_conn.cursor()
    try:
        # Enable WAL mode for better concurrency
        cursor.execute("PRAGMA journal_mode=WAL")
        # Optimize for performance
        cursor.execute("PRAGMA synchronous=NORMAL")  # Faster than FULL, safer than OFF
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache (negative = KB)
        cursor.execute("PRAGMA foreign_keys=ON")  # Enable foreign key constraints
        cursor.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
        cursor.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O
        cursor.close()
    except Exception as e:
        # If WAL mode fails (e.g., on network filesystems), continue without it
        print(f"Warning: Could not set SQLite pragmas: {e}")


# Create engine with optimized settings for multiprocessing
# Use QueuePool for better connection management
if settings.database_url.startswith("sqlite"):
    # For SQLite file DB, use QueuePool to avoid sharing the same connection across threads
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        poolclass=QueuePool,
        pool_size=max(5, settings.pool_size or 5),
        max_overflow=max(10, settings.max_overflow or 10),
        pool_pre_ping=settings.pool_pre_ping,
        echo=False,  # Set to True for SQL query logging in development
        future=True,  # Use 2.0 style
    )
else:
    # For other databases (PostgreSQL, MySQL, etc.), use QueuePool
    engine = create_engine(
        settings.database_url,
        poolclass=QueuePool,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=settings.pool_pre_ping,
        pool_recycle=settings.pool_recycle,
        pool_timeout=settings.pool_timeout,
        echo=False,
        future=True,
    )

# Plain session factory; create a new Session per request/task
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,  # Don't expire objects after commit (better for async)
)


def get_db():
    """
    Dependency that provides a database session.
    Thread-safe and optimized for multiprocessing.
    Use it in routes with: Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_context():
    """
    Context manager for database sessions (useful for background tasks).
    Thread-safe and automatically handles cleanup.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    """
    Initialize database connection and verify it works.
    Call this during app startup.
    """
    try:
        # Test connection (SQLAlchemy 2.0 style - use text() for raw SQL)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.commit()
        print("[OK] Database connection initialized successfully")
        return True
    except Exception as e:
        print(f"[FAIL] Database connection failed: {e}")
        return False


