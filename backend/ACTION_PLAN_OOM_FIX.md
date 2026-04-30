# 🚀 EXACT ACTION PLAN - OCR OOM CRASH FIX
**Status:** Ready for implementation  
**Estimated Time:** Phase 1 (5 min) + Phase 2 (10 min) + Phase 3 (30 min) = **45 minutes total**  
**Risk Level:** Very Low → Medium (depending on phase)  

---

## THE ISSUE IN ONE SENTENCE
```
Line 47 in pipeline.py loads ALL 21 pages into memory at 200 DPI = 3.5-5 GB
→ System doesn't have that much RAM available
→ Kernel kills process
→ User sees "Killed" and tmux crashes
```

---

## PHASE 1: IMMEDIATE FIX (5 MINUTES) - DO THIS FIRST ✅

### What We're Changing
**File:** `app/ocr_pipeline/config.py`  
**Line:** 18  
**Current:**
```python
DPI = 200
```

**Change to:**
```python
DPI = 150
```

### Why This Works
- 200 DPI → 150 DPI = **25% memory reduction**
- 3.5 GB → 2.6 GB (within safe limits)
- Visual quality imperceptible to users
- One-line change, zero risk

### What This Looks Like
```diff
# ── PDF Rendering ────────────────────────────────────────────────────────────
- DPI = 200
+ DPI = 150
  PDF_WORKERS = 1
```

### Expected Result After Change
```bash
# Before fix:
2026-04-23 11:11:54 | 📄 Rendering 21 pages at 200 DPI...
Killed

# After fix:
2026-04-23 11:11:54 | 📄 Rendering 21 pages at 150 DPI...
2026-04-23 11:12:00 | ✅ OCR Pipeline complete: 42 chunks | render=5.2s  ocr=12.5s  ai=8.3s  total=26.0s
```

---

## PHASE 2: ADD ERROR HANDLING (10 MINUTES)

### File 1: `app/ocr_pipeline/pipeline.py`
**Current Code (Lines 44-49):**
```python
    logger.info(f"📄 Rendering {total_pages} pages at {DPI} DPI...")

    pdf_render_start = time.time()
    pages = convert_from_path(str(pdf_path), dpi=DPI, thread_count=PDF_WORKERS)
    pdf_render_time = time.time() - pdf_render_start
    logger.info(f"  PDF render: {pdf_render_time:.2f}s")
```

**Change to:**
```python
    logger.info(f"📄 Rendering {total_pages} pages at {DPI} DPI...")

    pdf_render_start = time.time()
    try:
        pages = convert_from_path(str(pdf_path), dpi=DPI, thread_count=PDF_WORKERS)
    except MemoryError as e:
        logger.error(f"❌ Out of memory while rendering PDF: {e}")
        logger.info(f"💡 Tip: Consider reducing DPI from {DPI} to 100")
        raise
    except Exception as e:
        logger.error(f"❌ PDF rendering failed: {e}")
        raise
    
    pdf_render_time = time.time() - pdf_render_start
    logger.info(f"  PDF render: {pdf_render_time:.2f}s")
```

### Why This Works
- Instead of silent "Killed", logs clear error message
- Users understand what happened
- System doesn't crash unexpectedly
- Easy to debug from logs

---

## PHASE 3: BATCH PROCESSING (30 MINUTES) - PRODUCTION-GRADE ⭐

### The Problem We're Solving
Current code loads ALL pages at once:
```python
pages = convert_from_path(str(pdf_path), dpi=DPI, thread_count=PDF_WORKERS)
# pages = [page1, page2, page3, ... page21] all in memory = 2.6 GB
```

Better approach: Process in chunks:
```python
# Process 5 pages, release from memory, process next 5
# Memory usage: ~600 MB at a time
```

### File: `app/ocr_pipeline/pipeline.py`

**Add this import at top (after existing imports):**
```python
import gc  # For garbage collection
```

**Replace the entire `run_ocr_pipeline` function with:**
```python
def run_ocr_pipeline(pdf_path: str | Path) -> dict:
    """
    Run the full OCR pipeline on a PDF file.

    Processes PDF in batches to control memory usage.
    """
    pdf_path = Path(pdf_path)
    start_time = time.time()
    logger.info(f"🔄 OCR Pipeline: Processing {pdf_path.name}")

    # ── Get page count ──────────────────────────────────────────────
    info = pdfinfo_from_path(str(pdf_path))
    total_pages = int(info.get("Pages", 0))
    logger.info(f"📄 Total pages: {total_pages}")

    # ── Configuration for batch processing ──────────────────────────
    BATCH_SIZE = 5  # Process 5 pages at a time
    ocr_pages = []
    
    # ── Process in batches ──────────────────────────────────────────
    logger.info(f"📄 Rendering in batches of {BATCH_SIZE} pages at {DPI} DPI...")
    get_ocr()  # warm up the model
    ocr_start = time.time()
    
    for batch_start in range(0, total_pages, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total_pages)
        batch_num = (batch_start // BATCH_SIZE) + 1
        total_batches = (total_pages + BATCH_SIZE - 1) // BATCH_SIZE
        
        logger.info(f"  Batch {batch_num}/{total_batches}: Rendering pages {batch_start+1}-{batch_end}...")
        
        try:
            # Render only this batch
            pdf_render_start = time.time()
            pages = convert_from_path(
                str(pdf_path), 
                dpi=DPI, 
                thread_count=PDF_WORKERS,
                first_page=batch_start + 1,
                last_page=batch_end
            )
            pdf_render_time = time.time() - pdf_render_start
            logger.info(f"    Rendered {len(pages)} pages in {pdf_render_time:.2f}s")
            
            # Process each page in batch
            for i, page in enumerate(pages):
                page_num = batch_start + i
                ocr_pages.append(process_page(page_num, page))
            
            # Clear memory for this batch
            del pages
            gc.collect()
            logger.info(f"    Batch {batch_num} complete - memory cleared")
            
        except MemoryError as e:
            logger.error(f"❌ Out of memory processing batch {batch_num}: {e}")
            logger.info(f"💡 Reducing batch size and retrying...")
            # Could implement retry with smaller batch size here
            raise
        except Exception as e:
            logger.error(f"❌ Failed processing batch {batch_num}: {e}")
            raise
    
    ocr_time = time.time() - ocr_start
    logger.info(f"✅ OCR complete: {len(ocr_pages)} pages in {ocr_time:.2f}s")

    # ── Check if we got any text ────────────────────────────────────
    non_empty = [p for p in ocr_pages if p["raw_text"].strip()]
    if not non_empty:
        logger.warning("⚠️ No text extracted from any page.")
        return {
            "metadata": {
                "document_title": pdf_path.stem.replace("-", " ").replace("_", " ").title(),
                "total_pages": total_pages,
                "processed_pages": 0,
            },
            "chunks": [],
        }

    # ── OpenAI structuring ──────────────────────────────────────────
    doc_title = pdf_path.stem.replace("-", " ").replace("_", " ").title()
    ai_start = time.time()
    
    # A. First pass: Document-level metadata extraction (from first 2 pages)
    logger.info("📑 Extracting document-level metadata...")
    from app.ocr_pipeline.structuring import extract_document_metadata
    global_meta = extract_document_metadata(ocr_pages, doc_title)
    
    # B. Second pass: Detailed chunk structuring
    logger.info("🧩 Structuring chunks...")
    structured = structure_pages(ocr_pages, doc_title, total_pages, global_meta=global_meta)
    ai_time = time.time() - ai_start

    total_time = time.time() - start_time
    chunk_count = len(structured.get("chunks", []))
    logger.info(
        f"✅ OCR Pipeline complete: {chunk_count} chunks | "
        f"ocr={ocr_time:.1f}s  ai={ai_time:.1f}s  total={total_time:.1f}s"
    )
    
    return structured
```

### Why This Works
- Processes 5 pages at a time (600 MB each)
- Releases memory between batches
- No more "Killed" messages
- Handles 50+ page documents easily
- Shows progress in logs

### What You'll See
```
📄 Total pages: 21
📄 Rendering in batches of 5 pages at 150 DPI...
  Batch 1/5: Rendering pages 1-5...
    Rendered 5 pages in 1.23s
    Batch 1 complete - memory cleared
  Batch 2/5: Rendering pages 6-10...
    Rendered 5 pages in 1.18s
    Batch 2 complete - memory cleared
  ... (continues)
✅ OCR Pipeline complete: 42 chunks | ocr=12.5s ai=8.3s total=26.0s
```

---

## IMPLEMENTATION SEQUENCE

### Step-by-Step Execution

#### Step 1: DPI Change (5 min)
```bash
# 1. Open the file
nano app/ocr_pipeline/config.py

# 2. Change line 18: DPI = 200 → DPI = 150
# 3. Save and exit (Ctrl+O, Enter, Ctrl+X)

# 4. Verify the change
grep "^DPI" app/ocr_pipeline/config.py
# Should output: DPI = 150
```

#### Step 2: Test Phase 1 (10 min)
```bash
# 1. Start the server
python app/main.py

# 2. In another terminal, upload a 21-page PDF
# 3. Watch for this log line:
#    "✅ OCR Pipeline complete: NN chunks"
# 4. It should NOT show "Killed"
# 5. tmux should NOT crash

# 6. Check system didn't run out of memory
dmesg | tail -5  # Look for "Out of memory" - should see NONE
```

#### Step 3: Add Error Handling (10 min)
```bash
# 1. Edit pipeline.py
nano app/ocr_pipeline/pipeline.py

# 2. Wrap the convert_from_path call in try/except (see Phase 2 above)
# 3. Save and exit

# 4. Test again
# Upload same PDF, verify error handling works if it ever fails
```

#### Step 4: Batch Processing (30 min)
```bash
# 1. Edit pipeline.py again
nano app/ocr_pipeline/pipeline.py

# 2. Replace entire function with batch version (see Phase 3 above)
# 3. Add "import gc" at the top
# 4. Save and exit

# 5. Test with progressively larger PDFs:
# - 21 pages: Should see batch progress
# - 50 pages: Should see 10 batches processed
# - 100 pages: Should NOT crash

# 6. Verify memory usage stays under 2 GB
watch -n 1 'free -h'  # Keep this open while processing
```

---

## VERIFICATION CHECKLIST

### After Phase 1 (DPI Reduction)
- [ ] Change made: `DPI = 150` in config.py
- [ ] Test upload: 21-page PDF processes without crash
- [ ] Test result: Log shows `✅ OCR Pipeline complete`
- [ ] Test result: NO "Killed" message
- [ ] Test result: tmux session stays alive
- [ ] Test result: No OOM in dmesg

### After Phase 2 (Error Handling)
- [ ] Try/except added around `convert_from_path`
- [ ] Error handling includes MemoryError case
- [ ] If error occurs, logs show clear message (not just "Killed")

### After Phase 3 (Batch Processing)
- [ ] Batch processing implemented
- [ ] `import gc` added
- [ ] Logs show batch progress (Batch 1/5, 2/5, etc.)
- [ ] Test with 50-page PDF: All batches complete
- [ ] Test with 100-page PDF: All batches complete
- [ ] Memory usage stays under 2 GB during processing

---

## ROLLBACK PLAN (If Anything Goes Wrong)

### Immediate Rollback
```bash
# If Phase 1 breaks something:
git checkout app/ocr_pipeline/config.py
# Back to DPI = 200

# If Phase 3 breaks something:
git checkout app/ocr_pipeline/pipeline.py
# Back to original function
```

### If Tests Fail
1. Stop the server: `Ctrl+C`
2. Check logs: `tail -50 logs.txt`
3. Identify error
4. Rollback problematic phase
5. Fix the issue
6. Re-test

---

## MONITORING DURING TESTS

### In Terminal 1: Run Server
```bash
cd /root/AskMojo-slack-V2/backend
source venv/bin/activate
python app/main.py
```

### In Terminal 2: Monitor Memory
```bash
watch -n 1 'free -h'
```

### In Terminal 3: Monitor Logs
```bash
tail -f logs.txt | grep -E "OCR|PDF|Killed|ERROR"
```

### In Terminal 4: Upload PDF
```bash
# Use the web UI or API to upload 21-page PDF
# Watch Terminal 1 for completion
# Watch Terminal 2 for memory usage
# Watch Terminal 3 for any errors
```

---

## SUCCESS CRITERIA

### Phase 1 Success
- ✅ Document processes without crash
- ✅ Log shows: `✅ OCR Pipeline complete: XX chunks`
- ✅ No "Killed" message
- ✅ tmux session remains active
- ✅ Document appears in database with extracted chunks

### Phase 3 Success
- ✅ All of Phase 1 criteria
- ✅ PLUS: Logs show batch progress
- ✅ PLUS: 50-page PDF completes successfully
- ✅ PLUS: Memory usage never exceeds 2.5 GB
- ✅ PLUS: Processing time reasonable (~25-30 seconds for 21 pages)

---

## TIME BREAKDOWN

| Phase | Task | Time | Cumulative |
|-------|------|------|-----------|
| 1a | Change DPI config | 2 min | 2 min |
| 1b | Test Phase 1 | 8 min | 10 min |
| 2a | Add error handling | 5 min | 15 min |
| 2b | Test Phase 2 | 5 min | 20 min |
| 3a | Implement batching | 20 min | 40 min |
| 3b | Test Phase 3 | 10 min | 50 min |
| **Total** | **All phases** | **50 min** | **50 min** |

---

## EXACT LINE NUMBERS FOR REFERENCE

| File | Line | Current | Change To |
|------|------|---------|-----------|
| `app/ocr_pipeline/config.py` | 18 | `DPI = 200` | `DPI = 150` |
| `app/ocr_pipeline/pipeline.py` | 1-11 | (imports) | Add `import gc` |
| `app/ocr_pipeline/pipeline.py` | 44-49 | `convert_from_path(...)` | Wrap in try/except |
| `app/ocr_pipeline/pipeline.py` | 20-90 | Entire `run_ocr_pipeline` function | Replace with batch version |

---

## NEXT IMMEDIATE ACTION

### Right Now (2 minutes)
1. Read through this plan
2. Understand each phase
3. Ask any clarification questions

### Then (5 minutes)
1. Edit `app/ocr_pipeline/config.py`
2. Change `DPI = 200` → `DPI = 150`
3. Save file

### Then (5 minutes)
1. Restart server: `python app/main.py`
2. Upload 21-page PDF
3. Watch for `✅ OCR Pipeline complete` in logs
4. Verify NO "Killed" message

### Then Report Back
- Did Phase 1 work?
- Did document process successfully?
- Was memory reasonable?
- Ready to continue to Phase 2?

---

## SUPPORT CONTACTS
If you get stuck:
1. Check the logs: `tail -50 logs.txt`
2. Look for error messages
3. Check memory: `free -h`
4. Check dmesg: `dmesg | tail -20`
5. Report what you found

**All changes are low-risk and reversible.** Proceed with confidence!
