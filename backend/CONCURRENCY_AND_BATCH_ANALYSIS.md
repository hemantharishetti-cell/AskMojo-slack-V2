# 🔍 CONCURRENCY & BATCH PROCESSING ANALYSIS

## Current Configuration

### 1. **Document-Level Concurrency** (Multiple Documents)
```
MAX_CAPACITY = 15 documents per admin (processed in parallel)
```

**What this means:**
- When you upload multiple documents, up to 15 can be processed simultaneously
- Each document gets its own background task
- Documents are processed in parallel, NOT sequentially

**File:** `app/utils/concurrency.py`

---

### 2. **Page-Level Batch Processing** (Within Each Document)
```
BATCH_SIZE = 3 pages at a time
```

**What this means:**
- Each document is split into batches of 3 pages
- Batch 1: Pages 1-3 rendered at 200 DPI
- Batch 2: Pages 4-6 rendered at 200 DPI
- And so on...
- Memory is released between batches with `gc.collect()`

**File:** `app/ocr_pipeline/pipeline.py` (Line 51)

---

## 🚨 Why Multiple Document Upload Causes Crashes

```
Scenario: You upload 3 PDFs (each 21 pages, 200 DPI)

Timeline:
┌─────────────┐
│ Admin      │ MAX_CAPACITY = 15
├─────────────┤
│ Doc 1 start │─ Batch 1 (pages 1-3): ~900 MB
│ Doc 2 start │─ Batch 1 (pages 1-3): ~900 MB
│ Doc 3 start │─ Batch 1 (pages 1-3): ~900 MB
└─────────────┘

Total Memory = 900 MB + 900 MB + 900 MB = 2.7 GB
+ System overhead = ~3.5-4 GB

⚠️ If system has ~1.5 GB available = OOM KILL
```

---

## Solutions

### Option A: Process Only 1 Document At A Time
**Change:** MAX_CAPACITY from 15 → 1

```python
# app/utils/concurrency.py
MAX_CAPACITY = 1  # Process only 1 document at a time
```

**Pros:**
- ✅ Complete memory isolation between documents
- ✅ No parallel document processing overhead
- ✅ Guaranteed 100% stability
- ✅ Predictable resource usage

**Cons:**
- ❌ Slower overall throughput (must process documents sequentially)
- ❌ If Doc 1 takes 30s, Doc 2 must wait 30s
- ❌ Poor user experience if uploading multiple documents

**Memory Usage:**
- Single document, single batch = ~900 MB
- Safe and stable ✅

---

### Option B: Process 1 Page At A Time
**Change:** BATCH_SIZE from 3 → 1

```python
# app/ocr_pipeline/pipeline.py
BATCH_SIZE = 1  # Process 1 page at a time
```

**Pros:**
- ✅ Minimal memory per batch (~300 MB per page)
- ✅ Can still process multiple documents in parallel
- ✅ More precise memory control

**Cons:**
- ❌ More batches = more context switching
- ❌ Slightly slower (more gc.collect() calls)
- ⚠️ Still vulnerable if many docs upload simultaneously

**Memory Usage:**
- Single batch (1 page) = ~300 MB
- Multiple docs × 300 MB = could still crash with many documents

---

### Option C: RECOMMENDED - Both Changes (Best Solution)
**Change:** 
1. MAX_CAPACITY from 15 → 1 (one document at a time)
2. BATCH_SIZE from 3 → 1 (one page at a time)

```python
# app/utils/concurrency.py
MAX_CAPACITY = 1

# app/ocr_pipeline/pipeline.py
BATCH_SIZE = 1
```

**Pros:**
- ✅ Maximum stability - memory isolated per page
- ✅ Will handle any document size (100+ pages)
- ✅ Sequential processing = predictable
- ✅ Most reliable solution

**Cons:**
- ❌ Slowest throughput (one document, one page at a time)
- ❌ 21-page document might take ~45 seconds instead of 30

**Memory Usage:**
- Single page batch = ~300 MB
- Sequential processing = only 1 document active
- Total peak = ~1.2 GB ✅ Safe

---

## Analysis: Batch Size vs Page Count Trade-off

```
Document: 21 pages, 200 DPI

BATCH_SIZE = 1 (current recommended):
┌──────────────────────────────────────┐
│ Page 1  [300 MB] [2s] → Release mem  │
│ Page 2  [300 MB] [2s] → Release mem  │
│ ...                                  │
│ Page 21 [300 MB] [2s] → Release mem  │
├──────────────────────────────────────┤
│ Total time: ~42 seconds              │
│ Peak memory: ~300 MB ✅              │
└──────────────────────────────────────┘

BATCH_SIZE = 3 (current):
┌──────────────────────────────────────┐
│ Pages 1-3   [900 MB] [6s] → Release  │
│ Pages 4-6   [900 MB] [6s] → Release  │
│ ...                                  │
│ Pages 19-21 [600 MB] [4s] → Release  │
├──────────────────────────────────────┤
│ Total time: ~28 seconds              │
│ Peak memory: ~900 MB ✅ (if 1 doc)   │
└──────────────────────────────────────┘

BATCH_SIZE = 5 (original):
┌──────────────────────────────────────┐
│ Pages 1-5   [1.5 GB] [10s] → Release │
│ Pages 6-10  [1.5 GB] [10s] → Release │
│ ...                                  │
│ Pages 16-21 [1.2 GB] [8s] → Release  │
├──────────────────────────────────────┤
│ Total time: ~28 seconds              │
│ Peak memory: ~1.5 GB ⚠️              │
└──────────────────────────────────────┘
```

---

## 📊 Comparison Table

| Aspect | Option A | Option B | Option C (Recommended) |
|--------|----------|----------|----------------------|
| **Max Documents in Parallel** | 1 | 15 | 1 |
| **Pages per Batch** | 3 | 1 | 1 |
| **Peak Memory (Single Doc)** | ~900 MB | ~300 MB | ~300 MB |
| **Peak Memory (3 Docs at once)** | N/A | ~2.7 GB ⚠️ | N/A |
| **21-page Doc Time** | ~28s | ~42s | ~42s |
| **Stability** | ✅ Very stable | ⚠️ Medium | ✅✅ Very stable |
| **User Experience** | Slower | Medium | Slowest (but reliable) |
| **Crash Risk** | Very low | Medium | Very low |

---

## 🎯 My Recommendation

### Use **Option C**: Process 1 document, 1 page at a time

**Why:**
1. **Stability First** - You reported crashes with multiple docs. This completely eliminates that.
2. **Memory Predictable** - Peak usage is always ~300 MB per page
3. **Scalability** - Can handle documents of any size (100+, 500+ pages)
4. **Reliability** - No OOM killer surprises
5. **Maintainability** - Clear, simple processing model

**Trade-off:**
- Processing 21 pages takes ~42s instead of 28s
- That's ~1.5s per page, which is acceptable for OCR

**Alternative:**
If you need faster processing AND parallel document handling, use **Option B** (BATCH_SIZE = 1, keep MAX_CAPACITY = 15). This requires monitoring memory during peak uploads.

---

## Implementation

### To Implement Option C:

**File 1:** `app/utils/concurrency.py` (Change line 10)
```python
# From:
MAX_CAPACITY = 15

# To:
MAX_CAPACITY = 1
```

**File 2:** `app/ocr_pipeline/pipeline.py` (Change line 51)
```python
# From:
BATCH_SIZE = 3

# To:
BATCH_SIZE = 1
```

---

## What Should You Choose?

1. **If stability is your priority:** ✅ **Option C**
2. **If you need speed AND can monitor memory:** Option B
3. **If you want fast processing for single docs:** Option A + B

**Given your crash issues with multiple uploads → I recommend Option C**

---

## Current Status

- ✅ Document-level concurrency: MAX_CAPACITY = 15 (can cause crashes)
- ✅ Page-level batching: BATCH_SIZE = 3 (reasonable)
- ⚠️ Multiple documents crash issue: Needs concurrency reduction

Would you like me to:
1. Implement Option C (recommended)?
2. Implement Option B (balanced)?
3. Keep current setup?

Let me know! 🚀
