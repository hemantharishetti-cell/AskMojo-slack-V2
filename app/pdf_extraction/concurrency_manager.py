"""
Concurrency Manager for Document Processing

Limits parallel document processing to 15 per admin to prevent resource exhaustion.
Uses semaphore-based approach with admin-level tracking.
"""

import asyncio
import logging
from typing import Dict, Set
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.sqlite.models import DocumentUploadLog
from app.sqlite.database import SessionLocal

logger = logging.getLogger(__name__)

# Global tracking of concurrent processing per admin
# Structure: {admin_id: {"count": int, "semaphore": asyncio.Semaphore}}
_admin_semaphores: Dict[int, Dict] = {}
_concurrency_limit = 15  # Max 15 documents per admin in parallel


class ConcurrencyManager:
    """
    Manages concurrent document processing with per-admin limits.
    """
    
    @staticmethod
    def get_semaphore(admin_id: int) -> asyncio.Semaphore:
        """
        Get or create a semaphore for an admin.
        
        Args:
            admin_id: Admin user ID
            
        Returns:
            asyncio.Semaphore for this admin
        """
        if admin_id not in _admin_semaphores:
            _admin_semaphores[admin_id] = {
                "semaphore": asyncio.Semaphore(_concurrency_limit),
                "count": 0,
                "max_reached": False
            }
        
        return _admin_semaphores[admin_id]["semaphore"]
    
    @staticmethod
    async def acquire_slot(admin_id: int) -> bool:
        """
        Acquire a processing slot for an admin.
        Returns immediately (non-blocking).
        
        Args:
            admin_id: Admin user ID
            
        Returns:
            True if slot acquired, False if at capacity
        """
        semaphore = ConcurrencyManager.get_semaphore(admin_id)
        
        if semaphore._value > 0:
            await semaphore.acquire()
            _admin_semaphores[admin_id]["count"] += 1
            
            if semaphore._value == 0:
                _admin_semaphores[admin_id]["max_reached"] = True
                logger.warning(
                    f"Admin {admin_id} reached max concurrent processing limit "
                    f"({_concurrency_limit} documents)"
                )
            
            return True
        else:
            return False
    
    @staticmethod
    def release_slot(admin_id: int):
        """
        Release a processing slot for an admin.
        
        Args:
            admin_id: Admin user ID
        """
        if admin_id in _admin_semaphores:
            semaphore = _admin_semaphores[admin_id]["semaphore"]
            _admin_semaphores[admin_id]["count"] -= 1
            _admin_semaphores[admin_id]["max_reached"] = False
            semaphore.release()
            
            logger.debug(
                f"Released slot for admin {admin_id}. "
                f"Active: {_admin_semaphores[admin_id]['count']}/{_concurrency_limit}"
            )
    
    @staticmethod
    def get_concurrent_count(admin_id: int) -> int:
        """
        Get current concurrent processing count for an admin.
        
        Args:
            admin_id: Admin user ID
            
        Returns:
            Number of documents being processed
        """
        if admin_id in _admin_semaphores:
            return _admin_semaphores[admin_id]["count"]
        return 0
    
    @staticmethod
    def get_remaining_capacity(admin_id: int) -> int:
        """
        Get remaining processing slots for an admin.
        
        Args:
            admin_id: Admin user ID
            
        Returns:
            Number of slots available
        """
        current = ConcurrencyManager.get_concurrent_count(admin_id)
        return max(0, _concurrency_limit - current)
    
    @staticmethod
    def get_queue_length(admin_id: int, db: Session = None) -> int:
        """
        Get number of documents queued (waiting) for this admin.
        
        Args:
            admin_id: Admin user ID
            db: Database session (optional)
            
        Returns:
            Number of queued documents
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            # Count documents uploaded by this admin that haven't started processing
            queued = db.query(DocumentUploadLog).filter(
                and_(
                    DocumentUploadLog.uploaded_by == admin_id,
                    DocumentUploadLog.processing_started == False
                )
            ).count()
            
            return queued
        except Exception as e:
            logger.error(f"Error getting queue length: {str(e)}")
            return 0
        finally:
            if should_close:
                db.close()
    
    @staticmethod
    def get_stats(admin_id: int, db: Session = None) -> Dict:
        """
        Get detailed concurrency stats for an admin.
        
        Args:
            admin_id: Admin user ID
            db: Database session
            
        Returns:
            Stats dictionary
        """
        current = ConcurrencyManager.get_concurrent_count(admin_id)
        queued = ConcurrencyManager.get_queue_length(admin_id, db)
        remaining = ConcurrencyManager.get_remaining_capacity(admin_id)
        
        return {
            "admin_id": admin_id,
            "concurrent_processing": current,
            "queue_length": queued,
            "remaining_capacity": remaining,
            "max_capacity": _concurrency_limit,
            "utilization_percent": (current / _concurrency_limit) * 100,
        }


class ConcurrencyContextManager:
    """
    Context manager for handling concurrent slots.
    Automatically releases slot on completion.
    """
    
    def __init__(self, admin_id: int):
        """
        Initialize context manager.
        
        Args:
            admin_id: Admin user ID
        """
        self.admin_id = admin_id
        self.slot_acquired = False
    
    async def __aenter__(self):
        """Acquire slot on enter."""
        self.slot_acquired = await ConcurrencyManager.acquire_slot(self.admin_id)
        if not self.slot_acquired:
            raise RuntimeError(
                f"Could not acquire processing slot for admin {self.admin_id}. "
                f"Max capacity ({_concurrency_limit}) reached."
            )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release slot on exit."""
        if self.slot_acquired:
            ConcurrencyManager.release_slot(self.admin_id)
