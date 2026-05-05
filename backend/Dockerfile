# ── AskMojo Backend — Production Dockerfile ──────────────────────────────────
# Base: official Python 3.10 slim (Debian Bookworm, minimal footprint)
FROM python:3.10-slim

# ── System-level OS libraries ─────────────────────────────────────────────────
# Required by:
#   libglib2.0-0 libsm6 libxext6 libxrender1 libgl1  → OpenCV
#   poppler-utils   → pdf2image (pdfinfo_from_path + convert_from_path)
#   tesseract-ocr   → pytesseract fallback OCR
#   libgomp1        → PyTorch / scikit-learn OpenMP threading
#   build-essential → Cython native extensions (PaddleOCR)
#   curl            → Docker healthcheck + PaddleOCR model downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory inside container ───────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements BEFORE source so Docker caches the pip install layer.
# Code changes → only COPY . . re-runs (~seconds).
# requirements.txt changes → full pip install re-runs (~15-25 min).
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Application source code ───────────────────────────────────────────────────
COPY . .

# ── Runtime environment flags ─────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false \
    GLOG_minloglevel=3 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1

# ── Exposed port ──────────────────────────────────────────────────────────────
EXPOSE 8004

# ── Start server ──────────────────────────────────────────────────────────────
# --host 0.0.0.0  : bind all interfaces (127.0.0.1 is unreachable from outside container)
# --workers 1     : single worker — prevents multiple PaddleOCR model copies in RAM
# --loop asyncio  : explicit asyncio loop (most compatible on Linux)
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8004", \
     "--workers", "1", \
     "--loop", "asyncio"]
