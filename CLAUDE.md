# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Personalised Books is a platform for creating custom children's books with AI-powered face transfer. Users upload a child's photo, which is analyzed and used to generate personalized illustrations where the child's face appears in book illustrations.

## Architecture

### Services (Docker Compose)
- **web**: FastAPI backend API server (port 8000)
- **frontend**: React SPA with Vite (port 80)
- **comfyui**: ComfyUI server for image generation workflows (port 8188)
- **celery_worker**: GPU queue worker for face analysis and face swap tasks
- **celery_render_worker**: CPU queue worker for text rendering and PDF generation
- **db**: PostgreSQL database
- **redis**: Celery message broker

### Backend Structure (`backend/app/`)
- `main.py` - FastAPI app entry point with route registration
- `routes/` - API endpoints (auth, catalog, personalizations, cart, orders, account)
- `tasks.py` - Celery tasks for async processing (analyze_photo_task, build_stage_backgrounds_task, render_stage_pages_task)
- `workers.py` - Celery app configuration with task routing
- `inference/` - ML inference code
  - `comfy_runner.py` - ComfyUI workflow integration for face transfer
  - `vision_qwen.py` - Qwen2-VL for photo analysis
- `book/` - Book manifest handling
  - `manifest.py` - Pydantic models for book structure (PageSpec, TextLayer, BookManifest)
  - `manifest_store.py` - Loading manifests from templates
  - `stages.py` - Stage logic (prepay/postpay page determination)
- `rendering/html_text.py` - Text layer rendering with Playwright

### Frontend Structure (`faceapp-front/`)
React + TypeScript + Vite + Tailwind CSS
- `src/pages/` - PersonalizationPage, PreviewPage

### Book Templates (`backend/templates/{slug}/`)
Each book template contains:
- `manifest.json` - Book structure defining pages, face swap requirements, text layers, fonts
- `pages/` - Base illustration images
- `covers/` - Cover images
- `fonts/` - Custom fonts for text rendering
- `masks/` - Optional face masks for ComfyUI workflows

## Common Commands

### Backend Development
```bash
# Install dependencies (use Python 3.13 venv)
cd backend
python -m pip install -r requirements.txt

# Run API server locally
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run Celery worker
python -m celery -A app.workers.celery_app worker --loglevel=info

# Run tests
pytest backend/tests/
pytest backend/test_face_swap.py  # specific test file
```

### Frontend Development
```bash
cd faceapp-front
npm install
npm run dev      # development server
npm run build    # production build
npm run lint     # ESLint
```

### Docker
```bash
docker compose up -d              # start all services
docker compose up -d web frontend # start specific services
docker compose logs -f web        # follow logs
docker compose down               # stop all
```

## Key Workflows

### Personalization Pipeline
1. User uploads photo → `POST /upload_and_analyze/` creates Job, queues `analyze_photo_task` (GPU queue)
2. Analysis completes → Job status becomes `analyzing_completed`
3. User confirms with name/age → `POST /generate/` queues stage generation:
   - `build_stage_backgrounds_task` (GPU) - runs face swap via ComfyUI
   - `render_stage_pages_task` (CPU) - applies text layers
4. Prepay stage completes → status `prepay_ready`, preview available
5. Payment triggers postpay generation → status `completed`

### Face Transfer (ComfyUI)
`comfy_runner.py:run_face_transfer()`:
1. Loads illustration from S3
2. Optionally loads explicit mask or generates one via face detection
3. Uploads images to ComfyUI server
4. Builds workflow from `workflow_api.json` or `workflow.json`
5. Queues prompt, polls for completion, returns result image

## Environment Configuration

Copy `env.example` to `.env` and configure:
- `DATABASE_URL` - PostgreSQL connection
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` - Redis
- AWS S3 credentials for asset storage
- `COMFY_BASE_URL` - ComfyUI server URL
- `VITE_API_BASE_URL` - Backend URL for frontend builds

## Celery Task Queues
- `gpu` - Photo analysis, face swap (requires GPU)
- `render` - Text rendering, PDF generation (CPU-only)
- `celery` - Default queue

## S3 Storage Layout
- `child_photos/` - Uploaded child photos
- `avatars/` - User avatars
- `layout/{job_id}/pages/` - Generated page images
- `results/{job_id}/` - Legacy generated results
- `templates/{slug}/` - Book template assets
