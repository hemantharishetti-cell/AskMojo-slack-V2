"""
Adobe Rate Limiter

Tracks free-tier usage (500 extractions/month) and manages quota.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.config import settings
from app.sqlite.models import ExtractedContent
from app.sqlite.database import SessionLocal

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Manages Adobe free-tier rate limiting (500 extractions/month).
    """
    
    @staticmethod
    def get_current_month_usage(db: Session = None) -> int:
        """
        Get extraction count for current month.
        
        Args:
            db: Database session (optional)
            
        Returns:
            Number of extractions this month
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            count = db.query(func.count(ExtractedContent.id)).filter(
                and_(
                    ExtractedContent.extraction_source == "adobe_api",
                    ExtractedContent.extraction_date >= month_start,
                    ExtractedContent.error_message == None  # Only count successful extractions
                )
            ).scalar() or 0
            
            return count
            
        except Exception as e:
            logger.error(f"Error getting monthly usage: {str(e)}")
            return -1  # Return -1 to indicate error
        
        finally:
            if should_close:
                db.close()
    
    @staticmethod
    def get_remaining_quota(db: Session = None) -> int:
        """
        Get remaining extractions in monthly quota.
        
        Args:
            db: Database session
            
        Returns:
            Remaining extractions (0 if exceeded)
        """
        current_usage = RateLimiter.get_current_month_usage(db)
        
        if current_usage < 0:
            return -1  # Error
        
        remaining = max(0, settings.adobe_monthly_limit - current_usage)
        return remaining
    
    @staticmethod
    def can_extract(db: Session = None) -> Dict[str, Any]:
        """
        Check if extraction is allowed under current quota.
        
        Args:
            db: Database session
            
        Returns:
            Dict with 'allowed' boolean and 'reason' if not allowed
        """
        remaining = RateLimiter.get_remaining_quota(db)
        
        if remaining < 0:
            return {
                "allowed": False,
                "reason": "Error checking quota",
                "remaining": 0
            }
        
        if remaining == 0:
            return {
                "allowed": False,
                "reason": f"Monthly quota exceeded ({settings.adobe_monthly_limit} extractions)",
                "remaining": 0
            }
        
        # Alert at 90% usage
        if remaining <= settings.adobe_monthly_limit * 0.1:
            logger.warning(
                f"Adobe quota at {remaining}/{settings.adobe_monthly_limit}. "
                f"Alert threshold ({settings.adobe_monthly_limit * 0.1:.0f}) reached."
            )
        
        return {
            "allowed": True,
            "remaining": remaining,
            "total_limit": settings.adobe_monthly_limit
        }
    
    @staticmethod
    def record_extraction(
        document_id: int,
        extraction_method: str = "adobe_api",
        error_message: str = None,
        extraction_time_seconds: float = None,
        db: Session = None
    ) -> bool:
        """
        Record an extraction attempt in the database.
        
        Args:
            document_id: Document ID
            extraction_method: Method used
            error_message: Error message if extraction failed
            extraction_time_seconds: Time taken for extraction
            db: Database session
            
        Returns:
            True if recorded successfully
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            extracted = ExtractedContent(
                document_id=document_id,
                extraction_source=extraction_method,
                error_message=error_message,
                extraction_time_seconds=extraction_time_seconds,
                extraction_date=datetime.utcnow()
            )
            db.add(extracted)
            db.commit()
            
            current_usage = RateLimiter.get_current_month_usage(db)
            logger.info(
                f"Recorded extraction (method: {extraction_method}, "
                f"doc_id: {document_id}, monthly_usage: {current_usage}/{settings.adobe_monthly_limit})"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error recording extraction: {str(e)}")
            return False
        
        finally:
            if should_close:
                db.close()
    
    @staticmethod
    def get_usage_stats(db: Session = None) -> Dict[str, Any]:
        """
        Get detailed usage statistics.
        
        Args:
            db: Database session
            
        Returns:
            Dict with usage stats
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            current_usage = RateLimiter.get_current_month_usage(db)
            remaining = RateLimiter.get_remaining_quota(db)
            
            # Get success rate
            now = datetime.utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            total_attempts = db.query(func.count(ExtractedContent.id)).filter(
                and_(
                    ExtractedContent.extraction_source == "adobe_api",
                    ExtractedContent.extraction_date >= month_start
                )
            ).scalar() or 0
            
            failed_attempts = db.query(func.count(ExtractedContent.id)).filter(
                and_(
                    ExtractedContent.extraction_source == "adobe_api",
                    ExtractedContent.extraction_date >= month_start,
                    ExtractedContent.error_message != None
                )
            ).scalar() or 0
            
            success_rate = 0
            if total_attempts > 0:
                success_rate = ((total_attempts - failed_attempts) / total_attempts) * 100
            
            # Get average extraction time
            avg_time = db.query(func.avg(ExtractedContent.extraction_time_seconds)).filter(
                and_(
                    ExtractedContent.extraction_source == "adobe_api",
                    ExtractedContent.extraction_date >= month_start,
                    ExtractedContent.extraction_time_seconds != None
                )
            ).scalar() or 0
            
            return {
                "current_usage": current_usage,
                "monthly_limit": settings.adobe_monthly_limit,
                "remaining": remaining,
                "usage_percent": (current_usage / settings.adobe_monthly_limit) * 100,
                "total_attempts": total_attempts,
                "successful_extractions": current_usage,
                "failed_extractions": failed_attempts,
                "success_rate_percent": success_rate,
                "average_extraction_time_seconds": float(avg_time) if avg_time else 0,
            }
            
        except Exception as e:
            logger.error(f"Error getting usage stats: {str(e)}")
            return {}
        
        finally:
            if should_close:
                db.close()
