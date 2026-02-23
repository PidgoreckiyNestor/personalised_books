# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personalised Books is a platform for creating custom children's books with AI-powered face transfer. Users upload a child's photo, which is analyzed via Qwen2-VL and then used to generate personalized illustrations where the child's face appears in book pages via ComfyUI workflows (IPAdapter + ControlNet).

## Architecture

### Services (Docker Compose)

- **web**: FastAPI backend (port 8000)
- **frontend**: React SPA via nginx (port 80), proxies `/api/` to `http://web:8000/`
- **comfyui**: ComfyUI server for image generation (port 8188)
- **celery_worker**: GPU queue — face analysis, face swap
- **celery_render_worker**: CPU queue — text rendering (Playwright), PDF generation
- **db**: PostgreSQL (async via psycopg + SQLAlchemy)
- **redis**: Celery broker

### Docker Compose Variants

```bash
docker compose up -d                                                  # production
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d  # dev (MinIO, MOCK_ML, no GPU)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d  # GPU deploy
docker compose -f docker-compose.local.yml up -d                      # local infra only (MinIO + DB + Redis)
```

## Common Commands

### Backend

```bash
cd backend
python -m pip install -r requirements.txt          # install deps (Python 3.13 venv)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
python -m celery -A app.workers.celery_app worker --loglevel=info
python -m app.seed_data           # seed books catalog (add --drop to reset)
pytest backend/tests/             # run test suite
pytest backend/tests/test_api_basic.py::test_health  # single test
```

### Frontend

```bash
cd faceapp-front
npm install && npm run dev        # dev server with /api proxy to localhost:8000
npm run build                     # production build
npm run lint                      # ESLint
```

### Useful Scripts

```bash
scripts/setup_minio.sh            # create MinIO bucket + upload templates
backend/scripts/purge_jobs.py --yes  # delete all jobs/artifacts/cart/order items
backend/scripts/preview_book.py   # local book preview generation
```

## Key Design Decisions

### Database

- **No Alembic** — tables auto-created via `Base.metadata.create_all` on startup. New tables are added as separate models rather than altering existing ones. Schema changes to existing tables use one-off scripts in `backend/scripts/` (raw psycopg SQL).
- All PKs are `String` (UUID stored as text). No ORM relationships defined — all joins are manual queries.
- `expire_on_commit=False` on `AsyncSessionLocal` to prevent `MissingGreenlet` errors after commit.

### Celery + Asyncio Pattern

Celery tasks are synchronous but run async business logic via `asyncio.run()` inside each task, creating their own `AsyncSessionLocal()` context. This is intentional — Celery doesn't support native async in this setup.

### Config

Single `Settings` class in `backend/app/config.py` using `pydantic_settings.BaseSettings`. Key flags:

- `MOCK_ML=true` — skip real ML inference in dev (returns hardcoded analysis)
- `IPADAPTER_STRENGTH_SCALE` — scale IPAdapter weight in ComfyUI workflow
- `.env` is read automatically; `env.example` at root covers only ComfyUI proxy and `VITE_API_BASE_URL`

### Face Mask Fallback Chain

1. Explicit mask from S3 (`mask_{filename}`)
2. OpenCV Haar cascade auto-detected face → Gaussian-blurred ellipse
3. Centered ellipse fallback

### ComfyUI Workflow Injection

`comfy_runner.py:build_comfy_workflow()` handles two JSON formats:

- **API format** (`workflow_api.json`) — flat dict of nodes keyed by ID
- **UI format** (`workflow.json`) — `{nodes: [...], links: [...]}` structure

It scans by `class_type` to inject images, text prompts, seeds, and IPAdapter weights.

## Personalization Pipeline

```text
upload_photo → analyze_photo_task (GPU)
                    ↓
           analyzing_completed
                    ↓
confirm name/age → build_stage_backgrounds_task (GPU, face swap)
                    ↓
                  render_stage_pages_task (CPU, text overlay via Playwright)
                    ↓
                prepay_ready (preview available)
                    ↓
          payment → postpay generation → completed
```

Artifacts stored in S3: `layout/{job_id}/pages/page_{N:02d}_bg.png` (GPU output), `page_{N:02d}.png` (with text), `book.pdf` (generated on first download).

## Book Templates

Located in S3 at `templates/{slug}/`. Each template has:

- `manifest.json` — Pydantic-validated `BookManifest` defining pages, face swap flags, text layers with `str.format_map` templates (`{child_name}`, `{child_age}`, `{child_gender}`)
- `pages/`, `covers/`, `fonts/`, `masks/` directories

### Stage Logic

- **prepay**: First and last visible pages (pages 1 and 23 are hidden from frontend preview via `FRONT_HIDDEN_PAGE_NUMS`)
- **postpay**: All pages with `availability.postpay == True`
- Text rendering: Playwright headless Chromium, fonts loaded from S3 as base64 data URIs

## Frontend Conventions

The frontend (`faceapp-front/`) is React 19 + TypeScript + Vite 7 + Tailwind CSS 4. It uses direct `fetch()` calls (no API client library). `VITE_API_BASE_URL` defaults to `/api`.

The `reference/wonder_wraps_copy/` directory contains the target storefront design. Its `.cursorrules` defines TypeScript/React conventions (in Russian) that apply when building out the full frontend:

- Never use `any` — use concrete types or `unknown`
- Always `import type` for type-only imports
- Props types named with `Props` suffix
- Use `cn()` utility for conditional CSS classes, never template literals
- Event handlers prefixed with `handle`
- Named exports preferred; default exports only for page/route components
- Import order: React → external libs → internal `@app/` → `@shared/` → styles → types

## API Patterns

- Auth: JWT via `PyJWT` + `bcrypt`. Three dependency variants: strict (`get_current_user`), optional (`get_current_user_optional`), header-or-query-param (`get_current_user_header_or_query` for browser download links)
- Custom exceptions extend `FaceAppBaseException` (in `backend/app/exceptions.py`) with HTTP status codes
- Structured JSON logging via `python-json-logger` with request/response middleware

## S3 Storage Layout

```text
child_photos/{job_id}_{filename}       # uploaded photos
layout/{job_id}/pages/page_*_bg.png    # face-swapped backgrounds
layout/{job_id}/pages/page_*.png       # final pages with text
layout/{job_id}/book.pdf               # cached PDF (on-demand)
templates/{slug}/                      # book template assets
```

## Known Code Issues

- `personalizations.py` has duplicate route handler blocks from an incomplete merge — FastAPI uses the first registered handler, making later duplicates unreachable
- `_presigned_get()` S3 helper is copy-pasted across catalog.py, personalizations.py, and cart routes
- `normalize_child_name()` is duplicated in cart.py, orders.py, and personalizations.py
- No unique constraint on `carts.user_id` — duplicates are merged at read time in `services/cart.py`

## CI/CD

Only GitHub Action is `.github/workflows/docker-publish.yml` — builds and pushes ComfyUI Docker image to Docker Hub on changes to `comfyui/`. No CI for backend or frontend.
