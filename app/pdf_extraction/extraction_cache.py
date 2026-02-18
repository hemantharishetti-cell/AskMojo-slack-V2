"""
Extraction Cache Manager

Implements MD5-based caching to avoid reprocessing the same PDFs.
Respects TTL (time-to-live) for cache invalidation.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path
from sqlalchemy.orm import Session

from app.core.config import settings
from app.sqlite.models import PDFExtractionCache
from app.sqlite.database import SessionLocal

logger = logging.getLogger(__name__)


class ExtractionCacheManager:
    """
    Manages caching of PDF extraction results.
    
    Prevents redundant API calls by:
    - Computing MD5 hash of file content
    - Storing extracted JSON
    - Checking cache validity (TTL)
    - Tracking cache hit statistics
    """
    
    @staticmethod
    def compute_file_hash(file_path: str) -> str:
        """
        Compute MD5 hash of file content.
        
        Args:
            file_path: Path to file
            
        Returns:
            MD5 hash as hex string
        """
        md5_hash = hashlib.md5()
        
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    md5_hash.update(chunk)
            return md5_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error computing file hash: {str(e)}")
            return ""
    
    @staticmethod
    def get_cached_extraction(
        file_path: str,
        db: Session
    ) -> Optional[Dict[str, Any]]:
        """
        Check if extraction result exists in cache and is still valid.
        
        Args:
            file_path: Path to PDF file
            db: Database session
            
        Returns:
            Cached extraction JSON if valid, None otherwise
        """
        if not settings.adobe_cache_extraction_results:
            return None
        
        try:
            file_hash = ExtractionCacheManager.compute_file_hash(file_path)
            if not file_hash:
                return None
            
            cache_entry = db.query(PDFExtractionCache).filter(
                PDFExtractionCache.file_md5_hash == file_hash
            ).first()
            
            if not cache_entry:
                logger.debug(f"No cache entry for file hash: {file_hash}")
                return None
            
            # Check if cache has expired
            if cache_entry.expires_at and cache_entry.expires_at < datetime.utcnow():
                logger.info(f"Cache expired for file hash: {file_hash}")
                return None
            
            logger.info(f"Cache hit for file hash: {file_hash} (hits: {cache_entry.cache_hits + 1})")
            
            # Increment cache hit counter
            cache_entry.cache_hits += 1
            db.commit()
            
            # Parse and return cached JSON
            if cache_entry.adobe_extraction_json:
                try:
                    return json.loads(cache_entry.adobe_extraction_json)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in cache: {file_hash}")
                    return None
            
            return None
            
        except Exception as e:
            logger.error(f"Cache retrieval error: {str(e)}")
            return None
    
    @staticmethod
    def store_extraction_result(
        document_id: int,
        file_path: str,
        extraction_json: Dict[str, Any],
        extraction_method: str = "adobe_api",
        db: Session = None
    ) -> bool:
        """
        Store extraction result in cache.
        
        Args:
            document_id: Document ID
            file_path: Path to PDF file
            extraction_json: Extracted JSON from Adobe API
            extraction_method: Method used ("adobe_api" or "pdfplumber_fallback")
            db: Database session (optional, creates new if not provided)
            
        Returns:
            True if stored successfully, False otherwise
        """
        if not settings.adobe_cache_extraction_results:
            return False
        
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            file_hash = ExtractionCacheManager.compute_file_hash(file_path)
            if not file_hash:
                return False
            
            # Check if cache entry already exists
            existing = db.query(PDFExtractionCache).filter(
                PDFExtractionCache.file_md5_hash == file_hash
            ).first()
            
            if existing:
                logger.info(f"Updating cache for: {file_hash}")
                existing.adobe_extraction_json = json.dumps(extraction_json)
                existing.extraction_method = extraction_method
                existing.expires_at = datetime.utcnow() + timedelta(days=settings.adobe_cache_expiry_days)
            else:
                logger.info(f"Creating cache entry for: {file_hash}")
                cache_entry = PDFExtractionCache(
                    document_id=document_id,
                    file_md5_hash=file_hash,
                    adobe_extraction_json=json.dumps(extraction_json),
                    extraction_method=extraction_method,
                    expires_at=datetime.utcnow() + timedelta(days=settings.adobe_cache_expiry_days),
                    cache_hits=0
                )
                db.add(cache_entry)
            
            db.commit()
            return True
            
        except Exception as e:
            logger.error(f"Cache storage error: {str(e)}")
            return False
        
        finally:
            if should_close:
                db.close()
    
    @staticmethod
    def invalidate_cache(file_hash: Optional[str] = None, db: Session = None) -> bool:
        """
        Invalidate cache entries.
        
        Args:
            file_hash: Specific hash to invalidate (None = invalidate all)
            db: Database session
            
        Returns:
            True if successful
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            if file_hash:
                db.query(PDFExtractionCache).filter(
                    PDFExtractionCache.file_md5_hash == file_hash
                ).delete()
                logger.info(f"Invalidated cache for: {file_hash}")
            else:
                db.query(PDFExtractionCache).delete()
                logger.info("Invalidated all cache entries")
            
            db.commit()
            return True
            
        except Exception as e:
            logger.error(f"Cache invalidation error: {str(e)}")
            return False
        
        finally:
            if should_close:
                db.close()
    
    @staticmethod
    def get_cache_stats(db: Session = None) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Args:
            db: Database session
            
        Returns:
            Cache stats dictionary
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True
        
        try:
            total_entries = db.query(PDFExtractionCache).count()
            total_hits = db.query(PDFExtractionCache).with_entities(
                db.func.sum(PDFExtractionCache.cache_hits)
            ).scalar() or 0
            
            expired_count = db.query(PDFExtractionCache).filter(
                PDFExtractionCache.expires_at < datetime.utcnow()
            ).count()
            
            return {
                "total_cached_extractions": total_entries,
                "total_cache_hits": total_hits,
                "expired_entries": expired_count,
                "valid_entries": max(0, total_entries - expired_count),
            }
            
        except Exception as e:
            logger.error(f"Error getting cache stats: {str(e)}")
            return {}
        
        finally:
            if should_close:
                db.close()
