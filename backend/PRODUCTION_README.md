# AskMojo Backend — Production Deployment Guide

> **Server:** Ubuntu 24.04 LTS · 2 vCPU · 4 GB RAM · 20 GB Disk  
> **Stack:** FastAPI · PaddleOCR · ChromaDB · Jina Reranker · Slack Socket Mode  
> **Port:** 8004 (internal) · 80/443 via Nginx (public)

---

## 📁 Final Production File Structure

```
backend/
├── app/                          ← Application source (do not modify structure)
│   ├── adapters/                 ← Slack adapter layer
│   ├── admin/                    ← Admin panel routes
│   ├── api/                      ← New pipeline endpoints (ask, health)
│   ├── auth/                     ← JWT authentication
│   ├── core/
│   │   ├── config.py             ← Settings (reads from .env)
│   │   └── security.py
│   ├── ocr_pipeline/             ← PaddleOCR pipeline (batch, 1 page/batch)
│   ├── schemas/                  ← Pydantic schemas
│   ├── slack/                    ← Slack Socket Mode routes
│   ├── sqlite/                   ← SQLAlchemy models + migrations
│   ├── static/                   ← Admin UI (HTML/CSS/JS)
│   ├── user_api/                 ← User CRUD routes
│   ├── utils/                    ← Logging, concurrency, text utils
│   ├── vector_logic/             ← RAG pipeline (ChromaDB, reranker, router)
│   └── main.py                   ← FastAPI app entry point
│
├── monitoring/                   ← Error handler (Google Sheets + email alerts)
│
├── .dockerignore                 ← Excludes secrets, data dirs, caches
├── .env                          ← Secrets (NEVER commit to Git)
├── .gitignore
│
├── Dockerfile                    ← Multi-stage, CPU-only, non-root
├── docker-compose.yml            ← Resource limits + volumes + log rotation
├── nginx.prod.conf               ← Reverse proxy with gzip + rate limiting
├── deploy.sh                     ← First-time server setup script
│
├── requirements-prod.txt         ← Slim production dependencies only
│
├── create_admin.py               ← One-time: creates first admin user
└── reset_password.py             ← Utility: reset a user password
```

---

## 🚀 First-Time Deploy (Run Once on the Server)

### Step 1 — Upload code to server
```bash
# From your local machine:
rsync -avz --exclude='.git' --exclude='app/uploads' --exclude='app/sqlite' \
  --exclude='app/vector_db' --exclude='.paddleocr' \
  d:/Deplyment/askmojo-slack/backend/ root@YOUR_SERVER_IP:/opt/askmojo/backend/
```

### Step 2 — Edit `.env` on the server (4 values MUST change)
```bash
nano /opt/askmojo/backend/.env
```

Change these **4 values** before anything else:

| Variable | Dev value | Production value |
|----------|-----------|-----------------|
| `ENVIRONMENT` | `development` | **`production`** |
| `SECRET_KEY` | *(current hex)* | **Generate a new one** — see below |
| `CORS_ORIGINS` | `http://localhost:3000,...` | **`https://your-domain.com`** |
| `CHROMADB_PERSIST_DIRECTORY` | `./app/vector_db/chroma_db` | **`/app/chromadb`** |

**Generate a fresh SECRET_KEY:**
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### Step 3 — Run the deploy script
```bash
cd /opt/askmojo/backend
chmod +x deploy.sh
sudo ./deploy.sh
```

This script automatically:
- Installs Docker + Nginx
- Disables swap entirely
- Configures Nginx reverse proxy
- Sets up log rotation (10 MB × 3 files per container)
- Builds the Docker image
- Starts the container with resource limits
- Runs a health check

---

## 🔧 Day-to-Day Operations

### View live logs
```bash
docker compose logs -f --tail=100
```

### Restart the container
```bash
docker compose restart
```

### Deploy a code update
```bash
# 1. Upload new code (same rsync command as Step 1)
# 2. Rebuild and restart:
docker compose build --no-cache
docker compose up -d
```

### Create the first admin user (run once after first deploy)
```bash
docker compose exec askmojo-backend python create_admin.py
```

### Reset a user password
```bash
docker compose exec askmojo-backend python reset_password.py
```

### Check resource usage
```bash
docker stats askmojo_backend
```

### Check health
```bash
curl http://localhost:8004/api/health | python3 -m json.tool
```

---

## ⚙️ Resource Limits (Enforced by Docker)

| Limit | Value | Why |
|-------|-------|-----|
| RAM hard cap | 2,500 MB | Leaves 1.5 GB for OS + Nginx |
| Swap | Disabled | Prevents latency spikes |
| CPU | 1.8 vCPU | Leaves 0.2 vCPU for OS |
| Log size | 10 MB × 3 files | Prevents disk exhaustion |
| Workers | 1 | Prevents duplicate model loading |

---

## 📊 Expected RAM at Steady State

| Scenario | RAM Used |
|----------|----------|
| Idle (models loaded) | ~1.07 GB |
| 50 concurrent queries | ~1.87 GB ✅ |
| 100 concurrent queries | ~2.2 GB ✅ (staggered) |
| 1 OCR upload + 30 queries | ~1.90 GB ✅ |

---

## 🔒 Security Checklist

- [ ] `SECRET_KEY` replaced with generated 64-char hex key
- [ ] `CORS_ORIGINS` set to your actual frontend domain(s)
- [ ] `ENVIRONMENT=production` (triggers secret key guard on startup)
- [ ] `CHROMADB_PERSIST_DIRECTORY=/app/chromadb` (matches volume mount)
- [ ] `.env` file permissions: `chmod 600 .env`
- [ ] `.env` is in `.gitignore` (never committed)
- [ ] Nginx installed and serving on port 80/443
- [ ] SSL certificate from Let's Encrypt (`certbot --nginx`)

---

## 🩺 Health Check Response

A healthy server returns HTTP 200:
```json
{
  "status": "ok",
  "service": "askmojo",
  "timestamp": 1746487200,
  "checks": {
    "sqlite": "ok",
    "chromadb": "ok"
  }
}
```

A degraded server returns HTTP 503 with the failing subsystem named.

---

## ⚠️ Known Limits & Future Scaling

- **OCR uploads are CPU-bound** — concurrent uploads will queue (this is intentional with 1 worker)
- **Max safe concurrent OCR uploads: 1** — multiple simultaneous large PDF uploads may spike to ~2.1 GB RAM
- **Scaling trigger:** When sustained RAM > 2 GB or queue depth > 10 requests, add a second server
- **Next scaling step:** Move OCR to a background task queue (Huey + SQLite — no Redis needed)
