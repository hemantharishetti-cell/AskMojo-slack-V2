import logging
import asyncio
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)

class ConcurrencyManager:
    """
    Manages concurrent processing slots for admins.
    Max 15 parallel documents per admin.
    """
    # In-memory tracking of active slots: {admin_id: count}
    _active_slots: Dict[int, int] = {}
    _lock = asyncio.Lock()
    MAX_CAPACITY = 15

    @classmethod
    def get_concurrent_count(cls, admin_id: int) -> int:
        return cls._active_slots.get(admin_id, 0)

    @classmethod
    def get_remaining_capacity(cls, admin_id: int) -> int:
        return max(0, cls.MAX_CAPACITY - cls.get_concurrent_count(admin_id))

    @classmethod
    async def acquire_slot(cls, admin_id: int) -> bool:
        async with cls._lock:
            current = cls._active_slots.get(admin_id, 0)
            if current < cls.MAX_CAPACITY:
                cls._active_slots[admin_id] = current + 1
                return True
            return False

    @classmethod
    def release_slot(cls, admin_id: int):
        if admin_id in cls._active_slots:
            cls._active_slots[admin_id] = max(0, cls._active_slots[admin_id] - 1)

    @classmethod
    def get_stats(cls, admin_id: int, db: Session) -> Dict[str, Any]:
        """
        Get concurrency stats for an admin.
        """
        from app.sqlite.models import Document
        
        concurrent_count = cls.get_concurrent_count(admin_id)
        
        # Count documents in the queue (uploaded by this admin but not processed)
        queue_length = db.query(func.count(Document.id)).filter(
            Document.uploaded_by == admin_id,
            Document.processed == False
        ).scalar() or 0
        
        remaining = max(0, cls.MAX_CAPACITY - concurrent_count)
        utilization = (concurrent_count / cls.MAX_CAPACITY) * 100 if cls.MAX_CAPACITY > 0 else 0
        
        return {
            "admin_id": admin_id,
            "concurrent_processing": concurrent_count,
            "queue_length": queue_length,
            "remaining_capacity": remaining,
            "max_capacity": cls.MAX_CAPACITY,
            "utilization_percent": utilization
        }

class ConcurrencyContextManager:
    """
    Async context manager for safe slot acquisition/release.
    """
    def __init__(self, admin_id: int):
        self.admin_id = admin_id
        self.acquired = False

    async def __aenter__(self):
        self.acquired = await ConcurrencyManager.acquire_slot(self.admin_id)
        return self.acquired

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            ConcurrencyManager.release_slot(self.admin_id)
