"""
Health check endpoint for monitoring.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {"status": "ok", "service": "askmojo"}
