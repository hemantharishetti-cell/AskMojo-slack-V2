# Adobe PDF Services Integration

## Overview

AskMojo Slack now integrates with **Adobe PDF Services API** for high-quality PDF extraction and processing. This replaces the previous `pdfplumber`-only approach with a hybrid strategy.

## Integration Components

### Architecture

```
PDF Upload
    ↓
[Concurrency Manager] - Limits to 15 PDFs per admin
    ↓
[Adobe Extractor] ← Uses Access Token from Developer Console
    ↓
[Structure Checker]
    ├─ Success → [Structured Chunker]
    └─ Failure → [pdfplumber fallback]
    ↓
[Extraction Cache] - MD5-based deduplication (180 days TTL)
    ↓
[Rate Limiter] - Tracks 500/month free tier
    ↓
[Metadata Augmentation] - Adds heading hierarchy, sections
    ↓
[ChromaDB Vector Store]
```

### Key Modules

| Module | Purpose | Key Features |
|--------|---------|--------------|
| `adobe_client.py` | Adobe API wrapper | Upload, job polling, error handling |
| `extraction_cache.py` | Result caching | MD5 hashing, TTL validation, deduplication |
| `structured_chunking.py` | Smart chunking | Preserves heading hierarchy, max 1000 chars/chunk |
| `metadata_augmentation.py` | Metadata enrichment | Adds section paths, readability scores |
| `rate_limiter.py` | Quota tracking | 500 PDFs/month, monthly reset |
| `concurrency_manager.py` | Parallel control | Per-admin semaphores, 15 max docs |

## Authentication

### Current Setup (API Key + Access Token)

**Credentials Location:** `.env` file

```
ADOBE_API_KEY=031c4bad917b497480c9c7b0225b9ba8
ADOBE_ACCESS_TOKEN=eyJhbGciOiJSUzI1NiI...  (24-hour validity)
```

**Token Details:**
- Client ID: `031c4bad917b497480c9c7b0225b9ba8`
- Org: `8AA049DF6992F5B10A495C47@AdobeOrg`
- User: `8A1F48266992FFDA0A495F93@techacct.adobe.com`
- Scopes: `DCAPI, openid, AdobeID`
- Validity: 24 hours from issuance

### Token Refresh

When the current access token expires (after 24 hours), obtain a new one from:
1. Go to [Adobe Developer Console](https://developer.adobe.com/console)
2. Select your Project/App
3. View credentials (they'll show a fresh access token)
4. Update `ADOBE_ACCESS_TOKEN` in `.env`

## Configuration

### Environment Settings

```env
# API Credentials
ADOBE_API_KEY=...
ADOBE_ACCESS_TOKEN=...

# Extraction Settings
ADOBE_MONTHLY_LIMIT=500              # Free tier limit
ADOBE_FALLBACK_TO_PDFPLUMBER=true   # Fallback if Adobe fails
ADOBE_CACHE_EXTRACTION_RESULTS=true # Cache results
ADOBE_CACHE_EXPIRY_DAYS=180         # Cache TTL: 6 months

# Timeouts & Polling
ADOBE_EXTRACTION_TIMEOUT_SECONDS=300      # 5 minutes total
ADOBE_POLLING_INTERVAL_SECONDS=2         # Poll every 2s
ADOBE_POLLING_MAX_RETRIES=150            # Max polls (150 × 2s = 5min)
```

## API Limitations & Capabilities

### Supported
- ✅ PDF files up to **200 MB**
- ✅ Async job-based extraction
- ✅ Structured element extraction (text, headings, tables, lists)
- ✅ Metadata preservation (font, styling)
- ✅ 500 PDFs/month free tier

### Not Supported
- ❌ Scanned/image-based PDFs (without OCR flag)
- ❌ Password-protected PDFs
- ❌ Real-time extraction (async jobs only)
- ❌ Custom parameter tuning (fixed by Adobe)

## Rate Limiting & Quotas

### Free Tier
- **Budget:** 500 PDF extractions/month
- **Reset:** Monthly (1st of month)
- **Tracking:** Logged in database
- **Fallback:** pdfplumber if quota exceeded

### Per-Admin Concurrency
- **Limit:** 15 documents processing in parallel
- **Mechanism:** Async semaphore per admin user
- **Behavior:** Queues additional requests until slots free

## Caching Strategy

### Cache Key
- MD5 hash of PDF file content
- Unique per file (content-based, not filename)

### Benefits
- **Deduplication:** Same PDF uploaded twice = 1 credit used
- **Speed:** Cached results return instantly
- **Cost:** Extends free tier by ~50% on typical workflows

### Expiry
- **TTL:** 180 days (6 months)
- **Cleanup:** Auto-purged by background task
- **Size:** Limited by SQLite database size

## Database Schema

### PDFExtractionCache
```sql
id: Integer (primary key)
document_id: UUID (foreign key)
file_md5_hash: String (unique)
adobe_extraction_json: JSON
extraction_method: String ('adobe' | 'pdfplumber' | 'fallback')
cache_hits: Integer (for analytics)
created_at: DateTime
expires_at: DateTime
```

### ExtractedContent
```sql
id: Integer (primary key)
document_id: UUID (foreign key)
extraction_source: String ('adobe' | 'pdfplumber')
structured_json: JSON (chunked content)
extraction_date: DateTime
error_message: String (if failed)
retry_count: Integer
extraction_time_seconds: Float
```

## Integration Points

### Upload Workflow

1. **File Upload** (`POST /api/v1/documents/upload`)
   - Validates file size, format
   - Checks concurrency limits
   - Triggers background processing

2. **Processing** (Async background task)
   - Acquires concurrency slot
   - Checks extraction cache (MD5 lookup)
   - If cache hit: Use cached JSON
   - If miss: Call Adobe API
   - On Adobe failure: Fallback to pdfplumber
   - Structure & chunk content
   - Augment metadata
   - Record rate limit usage
   - Release concurrency slot

3. **Storage** (ChromaDB + SQLite)
   - Structured chunks → ChromaDB vectors
   - Metadata → SQLite ExtractedContent table
   - Cache entry → PDFExtractionCache table

### Query Workflow

When user issues a query:
1. Query is embedded and searched in ChromaDB
2. Retrieved document chunks include Adobe-extracted metadata
3. Heading hierarchy & section context from structured extraction
4. Improved answer quality vs. plain text extraction

## Testing

### Verify Setup

Run the test script to validate credentials:

```bash
python test_adobe_auth.py
```

Expected output:
```
[OK] Adobe API Key loaded
[OK] Adobe Access Token loaded
[OK] Token payload decoded
  - Scopes: DCAPI,openid,AdobeID
  - Token Expiry: 24.0 hours from issuance
[SUCCESS] ALL CHECKS PASSED
```

### Manual Testing

Upload a PDF through the admin panel. You should see:
- `PROCESSING` status with Adobe extraction
- Extraction time: 5-30 seconds
- Chunks created with heading hierarchy preserved
- Cache entry recorded if successful

## Troubleshooting

### "Access token not configured"
- **Cause:** `ADOBE_ACCESS_TOKEN` empty or missing from `.env`
- **Fix:** Get new token from Adobe Developer Console and update `.env`

### "Rate limit exceeded (500/month)"
- **Cause:** Free tier quota used up
- **Options:**
  1. Wait until next month (quota resets)
  2. Upgrade to paid plan in Adobe Developer Console
  3. Use pdfplumber fallback for additional PDFs

### "PDF exceeds 200MB"
- **Cause:** File too large for Adobe API
- **Fix:** Split PDF or use pdfplumber directly

### "Job polling timeout"
- **Cause:** Adobe service slow or PDF very complex
- **Fix:** Increase `ADOBE_POLLING_MAX_RETRIES` to 300 (10 min timeout)

### "Concurrency limit reached"
- **Cause:** Admin already has 15 PDFs processing
- **Fix:** Wait for current processing to complete or switch admin

## Performance Notes

### Speed
- **Upload → Extraction:** 5-30 seconds (depends on PDF complexity)
- **Cache hit:** <1 second
- **Chunking:** O(n) in PDF size
- **Embedding:** ~100 chunks/min (on typical GPU)

### Cost Analysis (Free Tier Usage)

**Scenario:** 500 PDFs/month, average 100 pages each

| Metric | Value |
|--------|-------|
| Total PDFs | 500 |
| Duplicate rate | 20% |
| Actual extractions needed | 400 (500 × 0.8) |
| Cost per extraction | 1 credit |
| **Total credits used** | **400 / 500** (80% quota) |

**With caching:** Saves 100 credit costs by deduplicating 20% of uploads

## Future Enhancements

- [ ] Implement token auto-refresh (schedule-based)
- [ ] Add OCR support for scanned PDFs (paid feature)
- [ ] Support password-protected PDFs
- [ ] Parallel job submission (batch 5 uploads at once)
- [ ] Cost estimation before processing
- [ ] Admin analytics dashboard

## References

- [Adobe PDF Services API Docs](https://developer.adobe.com/document-services/docs/overview/pdf-services-api/)
- [Extract API Reference](https://developer.adobe.com/document-services/docs/apis/pdf-extract/)
- [Authentication Guide](https://developer.adobe.com/developer-console/docs/guides/)
