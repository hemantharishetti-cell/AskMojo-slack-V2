# Multiprocessing Optimization Guide

This document explains the optimizations made to ASKMOJO Backend for efficient multiprocessing and concurrent access to SQLite and ChromaDB.

## Overview

The application has been optimized to handle multiple worker processes efficiently, with proper connection pooling, thread safety, and resource management.

## Key Optimizations

### 1. SQLite Database Optimization

#### WAL (Write-Ahead Logging) Mode
- **Enabled**: SQLite now uses WAL mode for better concurrency
- **Benefits**: 
  - Multiple readers can access the database simultaneously
  - One writer can write while readers are active
  - Better performance for read-heavy workloads
  - Reduced lock contention

#### Connection Pooling
- **Scoped Sessions**: Uses SQLAlchemy's `scoped_session` for thread-local sessions
- **Connection Management**: Automatic connection pooling with configurable pool size
- **Settings**:
  - `pool_size`: Scales with CPU cores (default: CPU count)
  - `max_overflow`: 20 connections for high concurrency
  - `pool_pre_ping`: Validates connections before use
  - `pool_recycle`: Recycles connections after 1 hour

#### SQLite Performance Tuning
- **Synchronous Mode**: Set to `NORMAL` (faster than FULL, safer than OFF)
- **Cache Size**: 64MB memory cache
- **Memory-Mapped I/O**: 256MB for faster disk access
- **Temp Store**: Uses memory for temporary tables

### 2. ChromaDB Optimization

#### Thread-Safe Client Access
- **Thread-Local Storage**: Each thread gets its own ChromaDB client instance
- **Singleton Pattern**: Prevents multiple client instances per thread
- **Connection Reuse**: Clients are reused within the same thread

#### Client Initialization
- **Lazy Loading**: ChromaDB client is created on first use
- **Connection Verification**: Startup check ensures ChromaDB is accessible
- **Error Handling**: Graceful degradation if ChromaDB is unavailable

### 3. App Startup Optimization

#### Lazy Loading
- **Routers**: Imported at module level (not in startup event)
- **Models**: Imported only when needed
- **Heavy Imports**: Deferred until necessary

#### Startup Sequence
1. Initialize SQLite connection
2. Run migrations
3. Create tables
4. Initialize ChromaDB
5. Load routers

#### Logging
- **Structured Logging**: Clear startup progress indicators
- **Error Handling**: Detailed error messages for debugging
- **Environment-Aware**: Different log levels for dev/prod

### 4. Multiprocessing Configuration

#### Worker Configuration
- **Development**: Single worker (for debugging)
- **Production**: Multiple workers (CPU count)
- **Embedding Generation**: Uses ProcessPoolExecutor with configurable workers

#### Settings
```python
pool_size: int = max(5, multiprocessing.cpu_count())
max_overflow: int = 20
max_workers: int = max(1, multiprocessing.cpu_count() - 1)
```

## Running with Multiple Workers

### Development Mode
```bash
python run.py
# Runs with 1 worker and auto-reload
```

### Production Mode
```bash
# Set environment to production
export ENVIRONMENT=production

# Run with multiple workers
python run.py
# Automatically uses CPU count workers
```

### Manual Worker Configuration
```bash
uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 4 \
    --log-level info
```

## Configuration Options

### Environment Variables

Add to `.env` file:

```env
# Database settings
DATABASE_URL=sqlite:///./app/sqlite/app.db
POOL_SIZE=10
MAX_OVERFLOW=20
SQLITE_TIMEOUT=20

# ChromaDB settings
CHROMADB_PERSIST_DIRECTORY=./app/vector_db/chroma_db

# Multiprocessing
MAX_WORKERS=4
ENVIRONMENT=production
```

### Key Settings Explained

- **POOL_SIZE**: Number of database connections to maintain
- **MAX_OVERFLOW**: Additional connections allowed beyond pool_size
- **SQLITE_TIMEOUT**: Seconds to wait for database lock
- **MAX_WORKERS**: Number of processes for embedding generation
- **ENVIRONMENT**: Set to "production" for multi-worker mode

## Performance Benefits

### Before Optimization
- Single worker only
- No connection pooling
- SQLite lock contention
- Sequential embedding generation

### After Optimization
- Multiple workers supported
- Efficient connection pooling
- WAL mode for better concurrency
- Parallel embedding generation
- Thread-safe ChromaDB access
- Optimized startup time

## Best Practices

### 1. Database Access
- Always use `get_db()` dependency in FastAPI routes
- Use `get_db_context()` for background tasks
- Don't share database sessions across threads

### 2. ChromaDB Access
- Use `_get_chroma_client()` for thread-safe access
- Don't create multiple clients manually
- Let the system manage client lifecycle

### 3. Background Tasks
- Use FastAPI's `BackgroundTasks` for async operations
- Use `get_db_context()` for database access in background tasks
- Ensure proper cleanup of resources

### 4. Production Deployment
- Set `ENVIRONMENT=production` for multi-worker mode
- Monitor connection pool usage
- Adjust `pool_size` based on load
- Use a reverse proxy (nginx) for load balancing

## Troubleshooting

### SQLite Lock Errors
- **Symptom**: "database is locked" errors
- **Solution**: Increase `SQLITE_TIMEOUT` in config
- **Check**: Ensure WAL mode is enabled (check database file)

### ChromaDB Connection Issues
- **Symptom**: ChromaDB initialization fails
- **Solution**: Check file permissions on persist directory
- **Check**: Verify ChromaDB is installed correctly

### High Memory Usage
- **Symptom**: Memory consumption grows over time
- **Solution**: Reduce `pool_size` and `max_overflow`
- **Check**: Monitor connection pool statistics

### Slow Startup
- **Symptom**: App takes long to start
- **Solution**: Check database size, optimize migrations
- **Check**: Verify ChromaDB collections aren't too large

## Monitoring

### Database Connection Pool
Monitor pool usage with SQLAlchemy events:
```python
from sqlalchemy import event
from app.sqlite.database import engine

@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    print(f"New connection: {dbapi_conn}")

@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_conn, connection_record, connection_proxy):
    print(f"Connection checked out from pool")
```

### ChromaDB Status
Check ChromaDB health:
```python
from app.vector_logic.vector_store import init_chromadb

if init_chromadb():
    print("ChromaDB is healthy")
```

## Migration Notes

### Existing Deployments
1. Backup your database before upgrading
2. The WAL mode will be enabled automatically on first connection
3. No data migration required
4. Existing connections will continue to work

### New Deployments
1. Set environment variables in `.env`
2. Run migrations: `python -m app.sqlite.migrations`
3. Start the application: `python run.py`
4. Monitor startup logs for any issues

## Additional Resources

- [SQLite WAL Mode Documentation](https://www.sqlite.org/wal.html)
- [SQLAlchemy Connection Pooling](https://docs.sqlalchemy.org/en/14/core/pooling.html)
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)
- [ChromaDB Documentation](https://docs.trychroma.com/)

