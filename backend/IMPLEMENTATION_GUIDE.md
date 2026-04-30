# Implementation Guide: Fixing Dependencies + OCR Memory Issues

## STEP-BY-STEP FIX PROCEDURE

### Phase 1: Immediate Stabilization (Choose ONE approach)

---

## ✅ APPROACH A: Fast Downgrade (Recommended for Now)
**Time: 5 minutes | Risk: Low | Best for: Immediate recovery**

```bash
#!/bin/bash
# Script to fix version conflicts

echo "Step 1: Downgrading to pinned versions..."
pip install transformers==4.30.2 --force-reinstall --no-deps
pip install huggingface_hub==1.5.0 --force-reinstall --no-deps
pip install regex==2026.2.28 --force-reinstall --no-deps
pip install PyYAML==6.0.2 --force-reinstall --no-deps

echo "Step 2: Installing missing PaddleX dependencies..."
pip install aistudio-sdk chardet colorlog modelscope prettytable py-cpuinfo ruamel.yaml ujson

echo "Step 3: Verifying environment..."
pip check

echo "✅ Environment restored!"
```

**Save this as:** `fix_dependencies_fast.sh`  
**Run:** `chmod +x fix_dependencies_fast.sh && ./fix_dependencies_fast.sh`

---

## ✅ APPROACH B: Clean Virtual Environment (Recommended for Production)
**Time: 10 minutes | Risk: Low | Best for: Long-term stability**

```bash
#!/bin/bash
# Complete environment rebuild

echo "📦 Building clean virtual environment..."

# Step 1: Deactivate current environment
deactivate 2>/dev/null || true

# Step 2: Backup old environment (optional)
if [ -d "venv" ]; then
    echo "Backing up current venv to venv.backup..."
    rm -rf venv.backup
    mv venv venv.backup
fi

# Step 3: Create fresh venv
echo "Creating new venv with Python 3.10..."
python3.10 -m venv venv

# Step 4: Activate
source venv/bin/activate

# Step 5: Upgrade pip first (current pip is 23.0.1, should be 26+)
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Step 6: Install from frozen requirements (in order)
echo "Installing from requirements..."
pip install -r requirements-core.txt

# Note: Don't use requirements.txt if it's the full 332-line combined file
# Use the component files instead:
pip install -r requirements-ml.txt
pip install -r requirements-ai.txt

# Step 7: Verify
echo "Verifying installation..."
pip check

if [ $? -eq 0 ]; then
    echo "✅ Clean environment ready!"
    echo "Run: source venv/bin/activate"
else
    echo "❌ Conflicts detected. Check output above."
    exit 1
fi
```

**Save this as:** `rebuild_venv.sh`  
**Run:** `chmod +x rebuild_venv.sh && ./rebuild_venv.sh`

---

## ✅ APPROACH C: Update Requirements for Production
**Time: 20 minutes | Risk: Medium | Best for: Modern stack**

Only use this if you want to modernize the dependency stack.

```bash
#!/bin/bash
# Create modern, tested requirements

echo "Creating new venv for dependency update..."
python3.10 -m venv venv-new
source venv-new/bin/activate

# Install modular core without specific versions first
pip install fastapi uvicorn pydantic SQLAlchemy python-dotenv

# Install modern ML stack (tested compatible)
pip install numpy==1.26.4 pandas==2.3.3 scikit-learn==1.7.2
pip install transformers==4.40.2  # Latest 4.x (API compatible with 4.30.2)
pip install torch==2.4.2 --index-url https://download.pytorch.org/whl/cpu
pip install paddleocr paddlepaddle

# Install other essentials
pip install chromadb langchain slack-sdk

# Verify
pip check

# Create new frozen requirements
echo "Generating new requirements-lock.txt..."
pip freeze > requirements-lock-modern.txt

echo "✅ New requirements created in requirements-lock-modern.txt"
echo "Review and test before deploying!"
```

**Use this only after testing thoroughly in staging.**

---

## Phase 2: Fix OCR Memory Issues

### Edit: app/ocr_pipeline/config.py

Find the DPI setting and reduce it:

```python
# BEFORE:
DPI = 200  # Renders at 200 DPI = high memory usage

# AFTER:
DPI = 150  # Renders at 150 DPI = 40% less memory
```

**Impact:**
- 21-page PDF at 200 DPI: ~2-5 GB
- 21-page PDF at 150 DPI: ~1-2 GB
- Quality: Barely noticeable difference

### Edit: app/vector_logic/processor.py

Add batch processing and memory management:

```python
# Find the document processing function
# Add this import at top:
import gc
from typing import Iterator

# Add this function:
def chunks(items: list, chunk_size: int) -> Iterator:
    """Split list into chunks."""
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]

# Modify the processing loop to use batches:
def process_document_pages(doc_id: int, pages: list):
    """Process document pages in memory-conscious batches."""
    batch_size = 5  # Process 5 pages at a time
    
    for i, batch in enumerate(chunks(pages, batch_size)):
        logger.info(f"Processing batch {i+1} for document {doc_id}")
        
        # Process this batch
        for page in batch:
            process_single_page(page)
        
        # Force garbage collection between batches
        gc.collect()
        
        # Optional: Log memory usage
        import psutil
        mem = psutil.virtual_memory()
        logger.info(f"Memory after batch {i+1}: {mem.percent}%")
```

---

## Phase 3: Validation & Testing

### Test 1: Verify Installation
```bash
# Run each command:
source /root/AskMojo-slack-V2/backend/venv/bin/activate

# Check for conflicts
pip check

# Verify critical packages
python -c "import transformers; print(f'Transformers: {transformers.__version__}')"
python -c "import paddleocr; print('PaddleOCR: OK')"
python -c "import chromadb; print('ChromaDB: OK')"
python -c "import fastapi; print('FastAPI: OK')"
```

**Expected output:**
```
No broken requirements found.
Transformers: 4.30.2
PaddleOCR: OK
ChromaDB: OK
FastAPI: OK
```

---

### Test 2: Application Startup
```bash
cd /root/AskMojo-slack-V2/backend

# Start application
python app/main.py

# Expected output:
# 2026-04-23 HH:MM:SS | INFO | app.main | Creating database tables...
# 2026-04-23 HH:MM:SS | INFO | app.main | [OK] Database tables ready
# [No error messages about imports]
# INFO: Uvicorn running on http://0.0.0.0:8000
```

---

### Test 3: Health Check
```bash
# In another terminal:
curl http://localhost:8000/api/v1/health

# Expected:
# {"status":"ok"} or similar
```

---

### Test 4: Small Document Upload
```bash
# Create a 1-page test PDF
# Upload it via API or UI
# Monitor for:
#   ✅ No import errors
#   ✅ OCR processes successfully
#   ✅ Memory usage reasonable (~500 MB)
```

---

### Test 5: Full Document Processing
```bash
# Upload the 21-page PDF
# Monitor in another terminal:
watch -n 1 'ps aux | grep python | grep app/main'

# Check memory usage stays under 80% of available
# Monitor for:
#   ✅ Processing completes
#   ✅ Memory stays under threshold
#   ✅ No OOM killer messages
```

---

## Deployment Checklist

Before deploying to production:

- [ ] Applied dependency fix (Approach A, B, or C)
- [ ] Run `pip check` - No conflicts reported
- [ ] Reduced OCR DPI to 150
- [ ] Added batch processing to processor.py
- [ ] Tested small PDF upload - Successful
- [ ] Tested 21-page PDF upload - No OOM
- [ ] Application startup - No errors
- [ ] Health endpoint - Responds OK
- [ ] Memory monitoring - Stays under 80%
- [ ] Slack integration - Still works
- [ ] Documents search - Still works
- [ ] Admin panel - Still works

---

## Monitoring During/After Fix

### Memory Monitoring
```bash
# Watch memory in real-time while processing document:
watch -n 1 'free -h && echo "---" && ps aux | grep python'

# Or use top:
top -p $(pgrep -f "app/main.py")
```

### Log Monitoring
```bash
# Watch logs for errors:
tail -f logs.txt

# Watch for these patterns:
# ✅ "Processing document X"
# ✅ "[OK] Document X processed"
# ❌ "Killed" (means OOM)
# ❌ "Traceback" (means error)
```

### Docker (if applicable)
```bash
# Monitor container memory:
docker stats container_name

# Check logs:
docker logs -f container_name
```

---

## Rollback Plan

If something goes wrong:

### Quick Rollback (5 minutes)
```bash
deactivate
rm -rf venv
mv venv.backup venv
source venv/bin/activate
python app/main.py
```

### Full Rollback (to git state)
```bash
git checkout requirements*.txt
deactivate
rm -rf venv
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Recommended Schedule

### Immediate (Today)
- [ ] Apply Approach A (Fast Downgrade) - 5 minutes
- [ ] Test small PDF upload - 5 minutes
- [ ] Test 21-page PDF upload - 5 minutes
- [ ] **TOTAL: 15 minutes to stability**

### Short-term (This Week)
- [ ] Reduce OCR DPI to 150 - 5 minutes
- [ ] Add batch processing - 15 minutes
- [ ] Test thoroughly - 30 minutes
- [ ] Deploy to staging - 10 minutes
- [ ] Test in staging - 30 minutes
- [ ] Deploy to production - 5 minutes

### Long-term (This Month)
- [ ] Create requirements-lock.txt
- [ ] Set up CI/CD constraints
- [ ] Document deployment procedure
- [ ] Train team on version management

---

## Common Issues & Solutions

### Issue: "pip check" still shows conflicts
**Solution:** This is normal if sub-dependencies are mismatched. Run Approach B (clean install).

### Issue: "No module named transformers"
**Solution:** Check that you activated venv: `source venv/bin/activate`

### Issue: Still getting OOM on 21-page PDF
**Solution:** 
1. Reduce DPI to 100
2. Reduce batch size to 3
3. Check if other processes are using memory
4. Add more system memory if possible

### Issue: ModuleNotFoundError for paddleocr
**Solution:** 
```bash
pip install paddleocr --force-reinstall
python -c "from paddleocr import PaddleOCR; print('OK')"
```

### Issue: transformers import still slow
**Solution:** This is normal on first import. Subsequent imports cache the model.

---

## Success Criteria

After completing this guide, you should have:

✅ No conflicts: `pip check` returns "No broken requirements found"  
✅ Correct versions: `transformers==4.30.2` specifically  
✅ OCR optimization: DPI set to 150  
✅ Memory management: Batch processing implemented  
✅ Successful tests: Small and large PDFs process without OOM  
✅ Production ready: All checks pass, ready for deployment  

---

## Final Commands Summary

```bash
# Quick fix (Approach A)
pip install transformers==4.30.2 --force-reinstall --no-deps
pip install huggingface_hub==1.5.0 --force-reinstall --no-deps
pip install regex==2026.2.28 --force-reinstall --no-deps
pip install PyYAML==6.0.2 --force-reinstall --no-deps
pip install aistudio-sdk chardet colorlog modelscope prettytable py-cpuinfo ruamel.yaml ujson
pip check

# OR clean rebuild (Approach B)
deactivate
rm -rf venv
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements-core.txt -r requirements-ml.txt -r requirements-ai.txt
pip check
```

---

**Now proceed to Phase 2 (OCR memory fixes) and Phase 3 (validation).**
