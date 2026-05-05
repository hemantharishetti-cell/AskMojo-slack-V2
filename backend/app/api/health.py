"""
Health check endpoint for monitoring.

Returns per-subsystem liveness so Docker / Nginx / uptime monitors
can distinguish a complete crash from a partial degradation.
"""

from __future__ import annotations
import time
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    Full liveness probe.
    Checks: SQLite DB connection + ChromaDB availability.
    Returns HTTP 200 when all healthy, HTTP 503 when any subsystem is down.
    """
    result: dict = {
        "status": "ok",
        "service": "askmojo",
        "timestamp": int(time.time()),
        "checks": {},
    }

    # ── SQLite check ──────────────────────────────────────────────────────────
    try:
        from app.sqlite.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        result["checks"]["sqlite"] = "ok"
    except Exception as exc:
        result["checks"]["sqlite"] = f"error: {exc}"
        result["status"] = "degraded"

    # ── ChromaDB check ────────────────────────────────────────────────────────
    try:
        from app.vector_logic.vector_store import _get_chroma_client
        _get_chroma_client().list_collections()
        result["checks"]["chromadb"] = "ok"
    except Exception as exc:
        result["checks"]["chromadb"] = f"error: {exc}"
        result["status"] = "degraded"

    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(content=result, status_code=status_code)

