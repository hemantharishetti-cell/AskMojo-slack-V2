# ── AskMojo Backend — Production Dockerfile ──────────────────────────────────
# Multi-stage build:  builder installs deps → final copies only runtime files.
# Base: python:3.10-slim (Debian Bookworm — glibc required by PaddlePaddle)
# NOTE: Alpine cannot be used — PaddlePaddle has no musl/Alpine wheel.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STAGE 1 — dependency builder
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FROM python:3.10-slim AS builder

# System build deps (only needed at install time — NOT copied to final image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /install

# Copy ONLY the slim production requirements (not the full 358-package dev dump)
COPY requirements-prod.txt .

# Install into an isolated prefix so we can copy only the site-packages
# Two extra indexes:
#   1. PyTorch CPU wheel — installs ~250 MB CPU-only torch instead of ~2.1 GB CUDA build
#   2. PaddlePaddle CPU wheel — CPU-only paddle (no CUDA drivers needed on server)
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ \
    -r requirements-prod.txt \
    --prefix=/install/deps \
    --no-warn-script-location

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STAGE 2 — lean production image
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FROM python:3.10-slim AS final

# Runtime OS libraries only
RUN apt-get update && apt-get install -y --no-install-recommends \
    # OpenCV headless runtime
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgl1 \
    # OpenMP (PyTorch / scikit-learn)
    libgomp1 \
    # pdf2image backend
    poppler-utils \
    # Tesseract fallback OCR
    tesseract-ocr \
    # Healthcheck probe
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Non-root user for container security
RUN groupadd --gid 1001 appuser \
 && useradd  --uid 1001 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

# Pull in installed Python packages from builder stage
COPY --from=builder /install/deps /usr/local

# Copy application source (respects .dockerignore)
COPY --chown=appuser:appuser . .

# Create writable directories for mounted volumes
RUN mkdir -p app/sqlite app/uploads chromadb \
 && chown -R appuser:appuser /app

# ── Runtime environment (production-safe defaults) ────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENVIRONMENT=production \
    # Thread limits — critical: prevents PaddleOCR/PyTorch from spawning 2× threads
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    # Suppress Paddle / HuggingFace noise
    TOKENIZERS_PARALLELISM=false \
    GLOG_minloglevel=3 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1 \
    FLAGS_allocator_strategy=naive_best_fit \
    # Force offline model use (no surprise downloads in prod)
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    ASKMOJO_RERANKER_LOCAL_ONLY=1

EXPOSE 8004

# Switch to non-root
USER appuser

# Health probe (used by Docker and docker-compose)
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:8004/api/health || exit 1

# ── Start server ──────────────────────────────────────────────────────────────
# 1 worker MANDATORY — prevents duplicate PaddleOCR model copies in RAM.
# timeout-keep-alive 5 frees idle connections faster.
# backlog 256 queues bursts without dropping connections.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8004", \
     "--workers", "1", \
     "--loop", "asyncio", \
     "--timeout-keep-alive", "5", \
     "--backlog", "256", \
     "--log-level", "warning", \
     "--no-access-log"]
