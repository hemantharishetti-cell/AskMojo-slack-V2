import uvicorn
import multiprocessing
from app.core.config import settings

if __name__ == "__main__":
    # Calculate optimal number of workers for multiprocessing
    # Use CPU count for production, 1 for development
    workers = 1 if settings.environment == "development" else (multiprocessing.cpu_count()-3)*2
    print(f"Using {workers} workers")
    
    # Run with multiple workers for better performance
    # uvicorn.run(
    #     "app.main:app",
    #     host=settings.host,
    #     port=settings.port,
    #     workers=workers,  # Multiple workers for multiprocessing
    #     reload=settings.environment == "development",  # Only reload in development
    #     log_level="info" if settings.environment == "development" else "warning",
    #     access_log=settings.environment == "development",
    # )

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=workers,  # Multiple workers for multiprocessing
        reload=settings.environment == "development"  # Only reload in development
    )