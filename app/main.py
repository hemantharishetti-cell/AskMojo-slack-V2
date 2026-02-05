from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
import os

from app.core.config import settings
from app.sqlite.database import Base, engine, init_db
from app.vector_logic.vector_store import init_chromadb

# Import routers (lazy loading happens at import time for better startup performance)
from app.user_api.routes import router as user_router
from app.vector_logic.routes import router as vector_router
from app.auth.routes import router as auth_router
from app.admin.routes import router as admin_router
from app.slack.routes import router as slack_router

# Configure logging
logging.basicConfig(
    level=logging.INFO if settings.environment == "development" else logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Set tokenizers parallelism if configured (to suppress warnings)
if settings.tokenizers_parallelism:
    os.environ["TOKENIZERS_PARALLELISM"] = settings.tokenizers_parallelism

app = FastAPI(
    title=settings.app_name,
    description="ASKMOJO Backend API - Optimized for multiprocessing",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers (at module level for better performance)
# Note: Tags are defined in each router, so we don't need to specify them here
app.include_router(auth_router, prefix="/api/v1")
app.include_router(user_router, prefix="/api/v1")
app.include_router(vector_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(slack_router, prefix="/api/v1")


@app.on_event("startup")
async def on_startup():
    """
    Optimized startup event:
    1. Initialize database connections
    2. Run migrations
    3. Create tables
    4. Initialize ChromaDB
    5. Lazy load routers
    """
    logger.info("Starting ASKMOJO Backend...")
    
    # Step 1: Initialize SQLite database connection
    logger.info("Initializing SQLite database...")
    if not init_db():
        logger.error("Failed to initialize database connection")
        raise RuntimeError("Database initialization failed")
    
    # Step 2: Run migrations (add missing columns)
    logger.info("Running database migrations...")
    try:
        from app.sqlite.migrations import run_migrations
        run_migrations()
    except Exception as e:
        logger.error(f"Migration error: {e}")
        # Don't fail startup if migrations have issues (might be already applied)
    
    # Step 3: Create tables (if they don't exist)
    logger.info("Creating database tables...")
    try:
        # Import models to register them with Base
        from app.sqlite import models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Database tables ready")
    except Exception as e:
        logger.error(f"Error creating tables: {e}")
        raise
    
    # Step 4: Initialize ChromaDB
    logger.info("Initializing ChromaDB...")
    if not init_chromadb():
        logger.warning("ChromaDB initialization failed - vector operations may not work")
    else:
        logger.info("✓ ChromaDB ready")
    
    # Step 5: Initialize Slack Socket Mode (if configured)
    logger.info("Checking Slack Socket Mode configuration...")
    try:
        from app.sqlite.database import SessionLocal
        from app.sqlite.models import SlackIntegration
        from app.slack.socket_mode import start_socket_mode_client
        
        db = SessionLocal()
        try:
            config = db.query(SlackIntegration).filter(
                SlackIntegration.is_active == True,
                SlackIntegration.socket_mode_enabled == True
            ).first()
            
            if config and config.app_token and config.bot_token:
                if start_socket_mode_client(config.app_token, config.bot_token):
                    logger.info("✓ Slack Socket Mode started")
                else:
                    logger.warning("Failed to start Slack Socket Mode")
            else:
                logger.info("Slack Socket Mode not configured or disabled")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Error initializing Slack Socket Mode: {e}")
    
    logger.info("✓ ASKMOJO Backend started successfully")


@app.on_event("shutdown")
async def on_shutdown():
    """Cleanup on shutdown."""
    logger.info("Shutting down ASKMOJO Backend...")
    # Close database connections
    engine.dispose()
    logger.info("✓ Shutdown complete")


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    """Redirect to admin panel login."""
    return RedirectResponse(url="/static/index.html")


