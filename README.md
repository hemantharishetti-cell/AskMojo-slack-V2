# askmojo-slack || python version -- 3.13.5

Slack bot for pre-sales and sales team. A FastAPI-based document Q&A backend with RAG (Retrieval Augmented Generation), Slack integration, and an admin panel. Users can upload documents, organize them by categories, and ask questions that are answered using AI-powered semantic search over the document corpus.

---

## Table of Contents

- [Features](#features)
- [Tech Stack & Dependencies](#tech-stack--dependencies)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [API Overview](#api-overview)
- [Utility Scripts](#utility-scripts)
- [Architecture](#architecture)

---

## Features

- **Document Management**: Upload PDFs, DOC, DOCX, TXT; auto-generate descriptions with OpenAI
- **Vector Search**: ChromaDB + Sentence Transformers for semantic search over document chunks
- **Q&A Endpoint**: `/ask` — natural language questions answered using retrieved context + OpenAI
- **Categories**: Organize documents into categories; each category maps to a ChromaDB collection
- **Slack Integration**: Ask questions via Slack (webhook or Socket Mode); register Slack users
- **Admin Panel**: User management, category management, query logs, upload logs
- **Authentication**: JWT-based auth with bcrypt password hashing; role-based access (user/admin)
- **TOON Compression**: Token optimization for OpenAI calls (via `toon-python` / `toon_format`)

---

## Tech Stack & Dependencies

| Package | Purpose |
|---------|---------|
| **fastapi** | Web framework; async API, automatic OpenAPI docs |
| **uvicorn[standard]** | ASGI server; runs the FastAPI app |
| **pydantic** / **pydantic-settings** | Data validation; settings from `.env` |
| **email-validator** | Email validation for Pydantic models |
| **SQLAlchemy** | ORM for SQLite; models, migrations, connection pooling |
| **chromadb** | Vector database for document embeddings |
| **sentence-transformers** | Embedding model (`all-MiniLM-L6-v2`) for semantic search |
| **pdfplumber** | Extract text from PDFs for chunking and description generation |
| **openai** | OpenAI API for descriptions and answer generation |
| **slack-sdk** | Slack API; webhooks, Socket Mode, chat.postMessage |
| **toon-python** / **toon_format** | Token compression for prompts (reduces API cost) |
| **tiktoken** | Token counting for usage tracking |
| **python-jose[cryptography]** | JWT creation and validation |
| **bcrypt** | Password hashing |
| **python-multipart** | Form data and file uploads |
| **httpx** | Async HTTP client (Slack API, internal calls) |
| **pytest** | Testing |

---

## Project Structure

```
ASKMOJO BACKEND/
├── app/
│   ├── main.py              # FastAPI app, CORS, static files, startup/shutdown
│   ├── core/
│   │   ├── config.py        # Settings (DB, JWT, ChromaDB, OpenAI, etc.)
│   │   └── security.py     # JWT, bcrypt, get_current_user, get_current_admin_user
│   ├── sqlite/
│   │   ├── database.py     # SQLAlchemy engine, session, init_db
│   │   ├── models.py       # User, Document, Category, QueryLog, SlackIntegration, etc.
│   │   └── migrations.py    # Schema migrations
│   ├── auth/
│   │   ├── routes.py       # /register, /login, /login-json, /me
│   │   └── schemas.py      # Token, UserLogin, UserRegister, UserAuthResponse
│   ├── admin/
│   │   ├── routes.py       # Users, categories, stats, query logs, upload logs
│   │   └── schemas.py      # AdminUserCreate, CategoryCreate, etc.
│   ├── user_api/
│   │   ├── routes.py       # User CRUD (create, list, get, update, delete)
│   │   └── schemas.py      # UserCreate, UserUpdate, UserResponse
│   ├── vector_logic/
│   │   ├── routes.py       # /upload, /documents, /search, /ask, /collections
│   │   ├── chunking.py     # chunk_by_pages (PDF → page-level chunks)
│   │   ├── processor.py    # Background document processing (chunk → embed → ChromaDB)
│   │   ├── description_generator.py  # OpenAI-based document descriptions
│   │   ├── vector_store.py # ChromaDB client, embeddings, query, collections
│   │   └── schemas.py      # DocumentResponse, AskRequest, AskResponse, etc.
│   ├── slack/
│   │   ├── routes.py       # Slack config, webhook, users, test
│   │   ├── socket_mode.py  # Slack Socket Mode (real-time events)
│   │   └── schemas.py      # SlackConfigCreate, SlackUserCreate, etc.
│   ├── uploads/            # Uploaded document files (gitignored)
│   └── vector_db/
│       └── chroma_db/      # ChromaDB persistence (gitignored)
├── static/                 # Admin panel (HTML, CSS, JS)
├── requirements.txt
├── run.py                  # Entry point (uvicorn with workers)
├── create_admin.py         # Create first admin user
├── reset_password.py       # Reset user password
├── .env                    # Environment variables (gitignored)
└── README.md
```

---

## Getting Started

### 1. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Environment variables

Create a `.env` file in the project root:

```env
# Required for AI features
OPENAI_API_KEY=sk-your-openai-api-key

# JWT (change in production)
SECRET_KEY=your-secret-key-change-in-production

# Optional overrides
ENVIRONMENT=development
HOST=127.0.0.1
PORT=8000
DATABASE_URL=sqlite:///./app/sqlite/app.db
CHROMADB_PERSIST_DIRECTORY=./app/vector_db/chroma_db
```

### 4. Create admin user

```bash
python create_admin.py
```

### 5. Run the server

```bash
python run.py
```

Or with uvicorn directly:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 6. Access the app

- **Admin panel**: http://127.0.0.1:8000/ (redirects to `/static/index.html`)
- **API docs**: http://127.0.0.1:8000/docs

---

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | `development` or `production` | `development` |
| `HOST` | Bind host | `127.0.0.1` |
| `PORT` | Bind port | `8000` |
| `DATABASE_URL` | SQLite connection string | `sqlite:///./app/sqlite/app.db` |
| `OPENAI_API_KEY` | OpenAI API key | Required for descriptions & answers |
| `SECRET_KEY` | JWT signing key | Change in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT expiry | `480` (8 hours) |
| `CHROMADB_PERSIST_DIRECTORY` | ChromaDB storage path | `app/vector_db/chroma_db` |
| `VECTOR_PROCESSING_DELAY` | Delay before processing uploads (seconds) | `5` |
| `MAX_WORKERS` | Embedding workers | `CPU count - 1` |
| `TOKENIZERS_PARALLELISM` | HuggingFace tokenizers | `true` or `false` |

---

## API Overview

All API routes are under `/api/v1`.

### Authentication (`/api/v1/auth`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/register` | Register new user |
| POST | `/login` | OAuth2 form login (email as username) |
| POST | `/login-json` | JSON login |
| GET | `/me` | Current user (requires Bearer token) |

### Admin (`/api/v1/admin`)

Requires admin role.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/stats` | Dashboard stats (users, documents, queries) |
| GET/POST/PUT/DELETE | `/users` | User management |
| GET/POST/PUT/DELETE | `/categories` | Category management |
| GET | `/logs/queries` | Query logs |
| GET | `/logs/uploads` | Upload logs |

### Documents & Q&A (`/api/v1`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload` | Upload document (admin) |
| GET | `/documents` | List documents |
| GET | `/documents/{id}` | Get document |
| PUT | `/documents/{id}` | Update document |
| DELETE | `/documents/{id}` | Delete document |
| GET | `/collections` | List ChromaDB collections |
| POST | `/search` | Vector search |
| POST | `/ask` | Ask a question (RAG) |

`POST /upload` is `multipart/form-data` and requires `file`, `title`, and `domain_id`.

### Slack (`/api/v1/slack`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST/PUT/DELETE | `/config` | Slack integration config |
| POST | `/webhook` | Slack Events API webhook |
| POST | `/test` | Send test message |
| GET/POST/PUT/DELETE | `/users` | Slack user registration |

---

## Utility Scripts

### Create admin user

```bash
python create_admin.py
```

Prompts for name, email, password. Creates or updates admin user.

### Reset password

```bash
python reset_password.py
```

Prompts for email and new password. Resets any user's password.

---

## Architecture

### Document flow

1. **Upload**: Admin uploads file → saved to `app/uploads/` → description generated (OpenAI) → record in SQLite.
2. **Processing**: Background task (after `VECTOR_PROCESSING_DELAY` seconds) chunks PDF with `pdfplumber`, generates embeddings with `sentence-transformers`, stores in ChromaDB.
3. **Query**: User asks question → embeddings for query → ChromaDB similarity search → top chunks + TOON compression → OpenAI completion → answer returned.

### Database migrations

Schema changes are handled by `app.sqlite.migrations` (custom migration utilities), not Alembic. Migrations run automatically on startup and via `create_admin.py`.

### Database models

- **User**: name, email, password (bcrypt), role (user/admin), is_active
- **Document**: title, category_id, description, file_path, processed
- **Category**: name, collection_name (ChromaDB), description
- **QueryLog**: user_id, query, answer, token usage, processing time
- **SlackIntegration**: bot_token, app_token, webhook_url, socket_mode_enabled
- **SlackUser**: slack_user_id, email, is_registered

### Slack integration

- **Webhook**: Slack sends events to `/api/v1/slack/webhook`; messages forwarded to `/ask`.
- **Socket Mode**: App connects to Slack via WebSocket; no public URL needed. Configure app token + bot token in admin panel.

### Multiprocessing

See `MULTIPROCESSING_OPTIMIZATION.md` for details on:

- SQLite WAL mode and connection pooling
- ChromaDB thread-safe client access
- Worker configuration for development vs production

---

## License

Proprietary. All rights reserved.
# AskMojo
# AskMojo
