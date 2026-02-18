import os
import sys
import uvicorn
import multiprocessing
from app.core.config import settings

# Fix Windows console encoding — stdout only (stderr must stay
# untouched because tqdm calls sys.stderr.flush() and a reconfigured
# stderr raises OSError [Errno 22] on Windows).
if os.name == "nt":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

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

    # uvicorn.run(
    #     "app.main:app",
    #     host=settings.host,
    #     port=settings.port,
    #     workers=workers,  # Multiple workers for multiprocessing
    #     reload=settings.environment == "development"  # Only reload in development
    # )
    uvicorn.run(
        "app.main:app",
        port=8001,
        reload=True  # Only reload in development
    )