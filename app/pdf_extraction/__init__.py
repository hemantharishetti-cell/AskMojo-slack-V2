"""
Adobe PDF Services API Integration Module

This module handles PDF extraction using Adobe's PDF Extract API,
with fallback to pdfplumber for reliability.

Key Components:
- adobe_client: Wrapper for Adobe API calls
- extraction_cache: MD5-based caching to avoid reprocessing
- structured_chunking: Intelligent chunking from Adobe JSON
- metadata_augmentation: Enriches chunks with heading/section metadata
- rate_limiter: Tracks free-tier usage (500 PDFs/month)
- concurrency_manager: Limits parallel processing (15 docs per admin)
"""

from app.pdf_extraction.adobe_client import AdobeExtractor
from app.pdf_extraction.extraction_cache import ExtractionCacheManager
from app.pdf_extraction.rate_limiter import RateLimiter
from app.pdf_extraction.concurrency_manager import ConcurrencyManager, ConcurrencyContextManager

__all__ = [
    'AdobeExtractor',
    'ExtractionCacheManager',
    'RateLimiter',
    'ConcurrencyManager',
    'ConcurrencyContextManager'
]
