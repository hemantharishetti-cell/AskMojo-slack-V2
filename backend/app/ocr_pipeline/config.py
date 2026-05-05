"""
OCR Pipeline Configuration

All tunable constants for the PaddleOCR + OpenAI structuring pipeline.
These can be overridden via app.core.config settings.
"""

import os

# ── Paddle environment flags (must be set before any paddle import) ──────────
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
os.environ.setdefault("FLAGS_allocator_strategy", "naive_best_fit")
os.environ.setdefault("GLOG_minloglevel", "3")

# ── PDF Rendering ────────────────────────────────────────────────────────────
DPI = 200
PDF_WORKERS = 1

# ── Memory-Safe Batch Rendering ───────────────────────────────────────────────
# Render + OCR this many pages at a time, then free memory before next batch.
# 3 pages ≈ peak ~900 MB (model 600 MB + 3×PIL 105 MB + numpy 80 MB + overhead)
# Safe limit on a 2.5 GB constrained server.
RENDER_BATCH_SIZE = 3

# ── Image Preprocessing ─────────────────────────────────────────────────────
MAX_SIDE_LIMIT = 1600
DARK_BG_THRESH = 127

# ── OCR ──────────────────────────────────────────────────────────────────────
CONF_THRESHOLD = 0.15  # Very lenient - captures almost all text with good word count

# ── OpenAI Structuring ───────────────────────────────────────────────────────
MODEL_NAME = "gpt-4o-mini"
BATCH_SIZE = 4
MAX_WORKERS = 3
