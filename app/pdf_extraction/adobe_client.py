"""
Adobe PDF Services API Client Wrapper

Handles API calls and response parsing for Adobe PDF Extract API.
Supports async operations with automatic OAuth2 token generation and caching.
Implements retry logic with exponential backoff.
"""

import httpx
import json
import logging
import time
import base64
import zipfile
import io
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime, timedelta
import asyncio

from app.core.config import settings

logger = logging.getLogger(__name__)


class AdobeTokenManager:
    """
    Manages OAuth2 access tokens for Adobe PDF Services API.
    
    Features:
    - Automatic token generation using Server-to-Server OAuth
    - In-memory caching with expiry tracking
    - Auto-refresh before token expires
    """
    
    def __init__(self):
        """Initialize token manager with OAuth credentials."""
        self.client_id = settings.adobe_client_id
        self.client_secret = settings.adobe_client_secret
        self.org_id = settings.adobe_org_id
        self.ims_endpoint = settings.adobe_ims_endpoint
        
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.token_expiry_buffer_seconds = 60  # Refresh 60s before actual expiry
        
        if not all([self.client_id, self.client_secret, self.org_id]):
            logger.warning("[ADOBE] OAuth credentials not fully configured. Token generation unavailable.")
    
    async def get_valid_token(self) -> Optional[str]:
        """
        Get a valid access token, refreshing if necessary.
        
        Returns:
            Valid access token, or None if generation failed
        """
        # Check if token exists and hasn't expired
        if self.access_token and self.token_expires_at:
            time_until_expiry = (self.token_expires_at - datetime.utcnow()).total_seconds()
            if time_until_expiry > self.token_expiry_buffer_seconds:
                logger.debug(f"[ADOBE] Using cached token (expires in {time_until_expiry:.0f}s)")
                return self.access_token
            else:
                logger.info(f"[ADOBE] Token expiring soon ({time_until_expiry:.0f}s), refreshing...")
        
        # Generate new token
        return await self._generate_token()
    
    async def _generate_token(self) -> Optional[str]:
        """
        Generate new OAuth2 access token using Server-to-Server flow.
        
        Returns:
            Access token, or None if generation failed
        """
        try:
            # Create base64 encoded credentials
            credentials = f"{self.client_id}:{self.client_secret}"
            credentials_b64 = base64.b64encode(credentials.encode()).decode()
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {
                    "Authorization": f"Basic {credentials_b64}",
                    "Content-Type": "application/x-www-form-urlencoded",
                }
                
                data = {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "openid,AdobeID,DCAPI",
                }
                
                logger.info("[ADOBE] Requesting new access token from IMS...")
                response = await client.post(self.ims_endpoint, headers=headers, data=data)
                
                if response.status_code not in (200, 201):
                    logger.error(f"[ADOBE] Token generation failed ({response.status_code}): {response.text}")
                    return None
                
                token_data = response.json()
                self.access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 3600)  # Default 1 hour
                
                # Set expiry time
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
                
                logger.info(f"[ADOBE] New token generated successfully (expires in {expires_in}s)")
                logger.debug(f"[ADOBE] Token preview: {self.access_token[:50]}...")
                
                return self.access_token
                
        except Exception as e:
            logger.error(f"[ADOBE] Token generation error: {str(e)}", exc_info=True)
            return None


class AdobeExtractor:
    """
    Wrapper for Adobe PDF Services Extract API.
    
    Handles:
    - File upload and async job creation
    - Job polling for completion
    - Response parsing
    - Automatic token generation and caching
    - Retry logic with exponential backoff
    """
    
    def __init__(self):
        """Initialize Adobe client with settings from config."""
        self.api_key = settings.adobe_api_key
        self.api_endpoint = settings.adobe_api_endpoint
        self.timeout = settings.adobe_extraction_timeout_seconds
        self.polling_interval = settings.adobe_polling_interval_seconds
        self.polling_max_retries = settings.adobe_polling_max_retries
        
        # Initialize token manager
        self.token_manager = AdobeTokenManager()
        
        if not all([settings.adobe_client_id, settings.adobe_client_secret, settings.adobe_org_id]):
            logger.warning("[ADOBE] OAuth credentials not fully configured. PDF extraction will fallback to pdfplumber.")
        else:
            logger.info("[ADOBE] AdobeExtractor initialized successfully with OAuth2 credentials")
    
    async def extract_pdf_async(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract PDF structure asynchronously using Adobe API.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            Parsed JSON with extracted elements, or None if extraction fails
        """
        try:
            # Ensure we have a valid token
            access_token = await self.token_manager.get_valid_token()
            if not access_token:
                logger.error("[ADOBE] Could not obtain access token - cannot extract")
                return None
            
            file_size = Path(file_path).stat().st_size
            if file_size > 200 * 1024 * 1024:  # 200 MB limit
                logger.warning(f"[ADOBE] PDF exceeds 200MB limit: {file_path}")
                return None
            
            logger.info(f"[ADOBE] Starting extraction for: {file_path} (size: {file_size / 1024 / 1024:.1f}MB)")
            start_time = time.time()
            
            # Step 1: Upload file and create extraction job
            job_id = await self._upload_and_create_job(file_path, access_token)
            if not job_id:
                logger.error(f"[ADOBE] Failed to create extraction job for: {file_path}")
                return None
            
            logger.info(f"[ADOBE] Created extraction job: {job_id}")
            
            # Step 2: Poll for job completion
            extracted_json = await self._poll_for_completion(job_id, access_token)
            if not extracted_json:
                logger.error(f"[ADOBE] Job did not complete successfully: {job_id}")
                return None
            
            elapsed = time.time() - start_time
            logger.info(f"[ADOBE] Extraction completed successfully in {elapsed:.2f}s for job {job_id}")
            
            return extracted_json
            
        except Exception as e:
            logger.error(f"[ADOBE] Extraction error: {str(e)}", exc_info=True)
            return None
    
    async def _upload_and_create_job(self, file_path: str, access_token: str) -> Optional[str]:
        """
        Upload PDF and create async extraction job at Adobe using the correct 3-step workflow.
        
        Step 1: Upload file to /assets → get assetID
        Step 2: Create extraction job at /operation/extractpdf with assetID
        
        Args:
            file_path: Path to PDF file
            access_token: Valid OAuth2 access token
            
        Returns:
            Job ID for polling, or None if failed
        """
        try:
            # STEP 1: Upload file to Adobe storage at /assets endpoint
            asset_id = await self._upload_asset(file_path, access_token)
            if not asset_id:
                logger.error(f"[ADOBE] Failed to upload asset for: {file_path}")
                return None
            
            logger.info(f"[ADOBE] Asset uploaded successfully: {asset_id}")
            
            # STEP 2: Create extraction job using assetID
            job_id = await self._create_extraction_job(asset_id, access_token)
            if not job_id:
                logger.error(f"[ADOBE] Failed to create extraction job for asset: {asset_id}")
                return None
            
            logger.info(f"[ADOBE] Extraction job created successfully: {job_id}")
            return job_id
                    
        except httpx.HTTPStatusError as e:
            logger.error(f"[ADOBE] HTTP error ({e.response.status_code}): {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"[ADOBE] Upload/job creation error: {str(e)}", exc_info=True)
            return None
    
    async def _upload_asset(self, file_path: str, access_token: str) -> Optional[str]:
        """
        Upload PDF file to Adobe using the 2-step OAuth asset upload flow:
        Step 1: POST JSON to /assets to get assetID and uploadUri
        Step 2: PUT raw PDF binary to uploadUri
        
        Args:
            file_path: Path to PDF file
            access_token: Valid OAuth2 access token
            
        Returns:
            Asset ID, or None if failed
        """
        try:
            logger.info(f"[ADOBE] Starting 2-step asset upload for: {Path(file_path).name}")
            
            # STEP 1: Create asset container and get uploadUri
            asset_id, upload_uri = await self._create_asset_container(access_token)
            if not asset_id or not upload_uri:
                logger.error("[ADOBE] Failed to create asset container")
                return None
            
            logger.info(f"[ADOBE] Asset container created: {asset_id}")
            logger.debug(f"[ADOBE] Upload URI: {upload_uri}")
            
            # STEP 2: Upload raw PDF binary to uploadUri
            success = await self._upload_binary_to_uri(file_path, upload_uri)
            if not success:
                logger.error("[ADOBE] Failed to upload binary to storage URI")
                return None
            
            logger.info(f"[ADOBE] Binary uploaded successfully to storage")
            return asset_id
                    
        except Exception as e:
            logger.error(f"[ADOBE] Asset upload error: {str(e)}", exc_info=True)
            return None
    
    async def _create_asset_container(self, access_token: str) -> tuple[Optional[str], Optional[str]]:
        """
        Step 1: Create asset container in Adobe and get upload URI.
        
        POST https://pdf-services.adobe.io/assets
        Body: {"mediaType": "application/pdf"}
        
        Returns:
            Tuple of (assetID, uploadUri), or (None, None) if failed
        """
        try:
            # Asset endpoint does NOT use /operation prefix
            create_asset_url = "https://pdf-services.adobe.io/assets"
            logger.info(f"[ADOBE] Creating asset container at: {create_asset_url}")
            
            headers = {
                "Authorization": f"Bearer {access_token}",
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
            }
            
            create_body = {
                "mediaType": "application/pdf"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    create_asset_url,
                    json=create_body,
                    headers=headers
                )
                
                logger.info(f"[ADOBE] Asset creation response status: {response.status_code}")
                logger.debug(f"[ADOBE] Asset creation response body: {response.text[:500]}")
                
                if response.status_code not in (200, 201):
                    logger.error(f"[ADOBE] Asset creation failed ({response.status_code}): {response.text}")
                    return None, None
                
                try:
                    asset_data = response.json()
                    asset_id = asset_data.get("assetID")
                    upload_uri = asset_data.get("uploadUri")
                    
                    if not asset_id or not upload_uri:
                        logger.error(f"[ADOBE] Missing assetID or uploadUri in response: {asset_data}")
                        return None, None
                    
                    logger.info(f"[ADOBE] Asset container created: {asset_id}")
                    return asset_id, upload_uri
                    
                except Exception as e:
                    logger.error(f"[ADOBE] Failed to parse asset creation response: {str(e)}")
                    return None, None
                    
        except Exception as e:
            logger.error(f"[ADOBE] Asset container creation error: {str(e)}", exc_info=True)
            return None, None
    
    async def _upload_binary_to_uri(self, file_path: str, upload_uri: str) -> bool:
        """
        Step 2: Upload raw PDF binary to the uploadUri returned by asset creation.
        
        PUT {uploadUri}
        Body: raw PDF bytes
        Content-Type: application/pdf
        
        Args:
            file_path: Path to PDF file
            upload_uri: Upload URI from asset creation response
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Read PDF file as binary
            with open(file_path, "rb") as f:
                pdf_bytes = f.read()
            
            file_size_mb = len(pdf_bytes) / 1024 / 1024
            logger.info(f"[ADOBE] Uploading {file_size_mb:.1f}MB to storage URI...")
            
            headers = {
                "Content-Type": "application/pdf",
            }
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.put(
                    upload_uri,
                    content=pdf_bytes,
                    headers=headers
                )
                
                logger.info(f"[ADOBE] Binary upload response status: {response.status_code}")
                logger.debug(f"[ADOBE] Binary upload response text: {response.text[:500] if response.text else '(empty)'}")
                
                if response.status_code not in (200, 201):
                    logger.error(f"[ADOBE] Binary upload failed ({response.status_code}): {response.text}")
                    return False
                
                logger.info("[ADOBE] Binary uploaded successfully to storage")
                return True
                    
        except Exception as e:
            logger.error(f"[ADOBE] Binary upload error: {str(e)}", exc_info=True)
            return False
    
    async def _create_extraction_job(self, asset_id: str, access_token: str) -> Optional[str]:
        """
        Create extraction job using uploaded assetID.
        
        POST https://pdf-services.adobe.io/operation/extractpdf
        Body: {"assetID": "...", "elementsToExtract": ["text", "tables"]}
        
        Args:
            asset_id: Asset ID from _upload_asset
            access_token: Valid OAuth2 access token
            
        Returns:
            Job ID, or None if failed
        """
        try:
            url = f"{self.api_endpoint}/extractpdf"
            logger.info(f"[ADOBE] Creating extraction job at: {url}")
            
            # Build JSON request body
            request_body = {
                "assetID": asset_id,
                "elementsToExtract": ["text", "tables"],
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "x-api-key": self.api_key,
                    "Content-Type": "application/json",
                }
                
                logger.debug(f"[ADOBE] Sending extraction job request: {request_body}")
                logger.debug(f"[ADOBE] Job creation URL: {url}")
                response = await client.post(url, json=request_body, headers=headers)
                
                logger.info(f"[ADOBE] Job creation response status: {response.status_code}")
                logger.info(f"[DEBUG] Full response headers: {dict(response.headers)}")
                logger.info(f"[DEBUG] Full response body: {response.text}")
                logger.info(f"[DEBUG] API endpoint is: {self.api_endpoint}")
                
                if response.status_code not in (200, 201, 202):
                    logger.error(f"[ADOBE] Job creation failed ({response.status_code}): {response.text}")
                    return None
                
                # Adobe returns job location in Location header, not in JSON body
                location = response.headers.get("Location") or response.headers.get("location")
                
                if not location:
                    logger.error(f"[ADOBE] No Location header in job creation response")
                    logger.debug(f"[ADOBE] Response body: {response.text[:500]}")
                    return None
                
                logger.info(f"[DEBUG] Location header value: {location}")
                
                # Extract job ID from URL: https://pdf-services.adobe.io/operation/extractpdf/{jobId}
                # Use safer extraction that validates format
                if "/extractpdf/" in location:
                    job_id = location.split("/extractpdf/")[-1].rstrip("/")
                else:
                    logger.error(f"[ADOBE] Unexpected Location header format (no /extractpdf/ in path): {location}")
                    return None
                
                logger.info(f"[DEBUG] Extracted job_id: {job_id}")
                
                if not job_id:
                    logger.error(f"[ADOBE] Could not extract job ID from Location header: {location}")
                    return None
                
                logger.info(f"[ADOBE] Extraction job created: {job_id}")
                logger.debug(f"[ADOBE] Location header: {location}")
                return job_id
                    
        except httpx.HTTPStatusError as e:
            logger.error(f"[ADOBE] Job creation HTTP error ({e.response.status_code}): {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"[ADOBE] Job creation error: {str(e)}", exc_info=True)
            return None
    
    async def _poll_for_completion(self, job_id: str, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Poll Adobe API for extraction job completion and retrieve results.
        
        When job is done, Adobe returns downloadUri pointing to ZIP file.
        Must download ZIP, extract structuredData.json, and parse.
        
        GET https://pdf-services.adobe.io/operation/extractpdf/{jobId}
        
        Args:
            job_id: Job ID from extraction job
            access_token: Valid OAuth2 access token
            
        Returns:
            Parsed structuredData.json from extraction, or None if failed
        """
        url = f"{self.api_endpoint}/extractpdf/{job_id}"
        retry_count = 0
        
        logger.info(f"[ADOBE] Starting polling for job {job_id}")
        logger.debug(f"[ADOBE] Poll endpoint: {url}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while retry_count < self.polling_max_retries:
                try:
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "x-api-key": self.api_key,
                    }
                    
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code == 404:
                        # Job not yet ready
                        logger.debug(f"[ADOBE] Job {job_id} not ready yet (404)")
                        retry_count += 1
                        await asyncio.sleep(self.polling_interval)
                        continue
                    
                    response.raise_for_status()
                    
                    data = response.json()
                    status = data.get("status")
                    
                    logger.debug(f"[ADOBE] Job {job_id} status: {status}")
                    logger.debug(f"[ADOBE] Poll response keys: {list(data.keys())}")
                    
                    if status == "done":
                        logger.info(f"[ADOBE] Job {job_id} completed successfully")
                        
                        # Adobe response structure: 
                        # "resource" → ZIP with structuredData.json (preferred)
                        # "content" → raw JSON (fallback)
                        download_uri = None
                        
                        # Preferred: structured ZIP file
                        if "resource" in data and isinstance(data["resource"], dict):
                            download_uri = data["resource"].get("downloadUri")
                            if download_uri:
                                logger.info("[ADOBE] Using resource (ZIP) for extraction")
                        
                        # Fallback: direct JSON content
                        if not download_uri and "content" in data and isinstance(data["content"], dict):
                            download_uri = data["content"].get("downloadUri")
                            if download_uri:
                                logger.info("[ADOBE] Using content (JSON) as fallback")
                        
                        if not download_uri:
                            logger.error(f"[ADOBE] No downloadUri found in response. Available keys: {list(data.keys())}")
                            logger.debug(f"[ADOBE] Full response: {data}")
                            return None
                        
                        logger.info(f"[ADOBE] Downloading extraction results from: {download_uri}")
                        
                        # Download and extract ZIP file
                        extracted_data = await self._download_and_extract_results(client, download_uri)
                        
                        if not extracted_data:
                            logger.error(f"[ADOBE] Failed to extract results ZIP for job {job_id}")
                            return None
                        
                        logger.info(f"[ADOBE] Successfully extracted and parsed results")
                        logger.debug(f"[ADOBE] Extraction result keys: {list(extracted_data.keys())}")
                        return extracted_data
                    
                    if status in ["failed", "error"]:
                        logger.error(f"[ADOBE] Job {job_id} failed with status '{status}': {data}")
                        return None
                    
                    logger.debug(f"[ADOBE] Job {job_id} still processing (status: {status})...")
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 404:
                        logger.error(f"[ADOBE] Poll error ({e.response.status_code}): {e.response.text}")
                        return None
                except Exception as e:
                    logger.error(f"[ADOBE] Poll error: {str(e)}", exc_info=True)
                    return None
                
                retry_count += 1
                await asyncio.sleep(self.polling_interval)
        
        logger.error(f"[ADOBE] Job {job_id} polling timeout after {self.polling_max_retries} retries")
        return None
    
    async def _download_and_extract_results(self, client: httpx.AsyncClient, download_uri: str) -> Optional[Dict[str, Any]]:
        """
        Download ZIP file from Adobe and extract structuredData.json.
        
        Args:
            client: Async HTTP client
            download_uri: URI to ZIP file from Adobe
            
        Returns:
            Parsed structuredData.json, or None if failed
        """
        try:
            logger.info(f"[ADOBE] Downloading ZIP from: {download_uri}")
            
            # Download ZIP file
            response = await client.get(download_uri, timeout=60.0)
            
            if response.status_code != 200:
                logger.error(f"[ADOBE] Failed to download ZIP ({response.status_code}): {response.text}")
                return None
            
            zip_bytes = response.content
            logger.info(f"[ADOBE] Downloaded {len(zip_bytes)} bytes")
            
            # Extract ZIP and find structuredData.json
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
                    logger.debug(f"[ADOBE] ZIP contents: {zip_file.namelist()}")
                    
                    # Look for structuredData.json in ZIP
                    structured_data_path = None
                    for file_name in zip_file.namelist():
                        if file_name.endswith("structuredData.json"):
                            structured_data_path = file_name
                            break
                    
                    if not structured_data_path:
                        logger.error(f"[ADOBE] structuredData.json not found in ZIP. Contents: {zip_file.namelist()}")
                        return None
                    
                    logger.info(f"[ADOBE] Found: {structured_data_path}")
                    
                    # Read and parse structuredData.json
                    with zip_file.open(structured_data_path) as json_file:
                        extracted_data = json.load(json_file)
                    
                    logger.info(f"[ADOBE] Parsed structuredData.json successfully")
                    logger.debug(f"[ADOBE] Top-level keys: {list(extracted_data.keys())}")
                    
                    # Log element count and samples
                    elements = extracted_data.get("elements", [])
                    logger.info(f"[ADOBE] Found {len(elements)} elements in extraction")
                    
                    # Log element type distribution
                    element_types = {}
                    for elem in elements:
                        path = elem.get("Path", "")
                        # Extract type from path: //Document/H1 → H1
                        if "/" in path:
                            elem_type = path.split("/")[-1]
                            element_types[elem_type] = element_types.get(elem_type, 0) + 1
                    
                    if element_types:
                        logger.info(f"[ADOBE] Element types distribution: {element_types}")
                    
                    # Log first 3 elements as samples
                    if elements:
                        logger.info("[ADOBE] Sample elements:")
                        for idx, elem in enumerate(elements[:3]):
                            text_preview = elem.get("Text", "")[:100]
                            path = elem.get("Path", "")
                            page = elem.get("Page", "N/A")
                            logger.info(f"  [{idx+1}] {path} (Page {page}): {text_preview}...")
                    
                    return extracted_data
                    
            except zipfile.BadZipFile as e:
                logger.error(f"[ADOBE] Invalid ZIP file: {str(e)}")
                return None
            except json.JSONDecodeError as e:
                logger.error(f"[ADOBE] Failed to parse JSON from ZIP: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"[ADOBE] Download/extraction error: {str(e)}", exc_info=True)
            return None
    
    def extract_pdf_sync(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Extract PDF synchronously (blocking). 
        
        This is a wrapper around the async method for synchronous contexts.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            Parsed JSON with extracted elements
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If already in async context, create new loop
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                result = new_loop.run_until_complete(self.extract_pdf_async(file_path))
                new_loop.close()
                return result
            else:
                return loop.run_until_complete(self.extract_pdf_async(file_path))
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(self.extract_pdf_async(file_path))


# Singleton instance
_extractor_instance = None


def get_adobe_extractor() -> AdobeExtractor:
    """Get or create Adobe extractor singleton."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = AdobeExtractor()
    return _extractor_instance
