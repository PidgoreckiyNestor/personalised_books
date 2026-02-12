# Загальний огляд проєкту Personalised Books

## Що це

Платформа для створення персоналізованих дитячих книг з AI-powered face transfer. Користувач завантажує фото дитини, система аналізує обличчя і генерує ілюстрації де обличчя дитини з'являється в книжкових ілюстраціях. Потім накладається текст з ім'ям дитини, і книга готова до друку.

---

## Архітектура сервісів (Docker Compose)

```
                        +-------------+
                        |  Frontend   |  React + Vite + Tailwind
                        |  (nginx:80) |  SPA, 2 сторінки
                        +------+------+
                               |
                               | /api/* proxy
                               v
                        +------+------+
                        |    Web      |  FastAPI (port 8000)
                        |  (backend)  |  REST API, auth, routes
                        +--+----+--+--+
                           |    |  |
              +------------+    |  +------------+
              |                 |               |
              v                 v               v
      +-------+---+     +------+------+  +------+------+
      |    DB     |     |   Redis     |  |     S3      |
      | PostgreSQL|     |  (broker)   |  | (Yandex/    |
      |  (5432)   |     |  (6379)     |  |  MinIO)     |
      +-----------+     +--+----+-----+  +-------------+
                           |    |
              +------------+    +------------+
              |                              |
              v                              v
      +-------+--------+          +---------+----------+
      | celery_worker   |          | celery_render_worker|
      | (GPU queue)     |          | (CPU queue)         |
      | - analyze photo |          | - text rendering    |
      | - face swap     |          | - PDF generation    |
      +-------+---------+          +--------------------+
              |
              v
      +-------+--------+
      |    ComfyUI     |
      | (port 8188)    |
      | - Stable Diff  |
      | - IPAdapter    |
      | - ControlNet   |
      +----------------+
```

| Сервіс | Образ | GPU | Порт | Призначення |
|--------|-------|-----|------|-------------|
| **web** | backend/Dockerfile | - | 8000 | FastAPI API сервер |
| **frontend** | faceapp-front/Dockerfile | - | 80 | React SPA (nginx) |
| **comfyui** | comfyui/Dockerfile | Yes | 8188 | Stable Diffusion + face swap |
| **celery_worker** | backend/Dockerfile | Yes | - | GPU таски (аналіз, face swap) |
| **celery_render_worker** | backend/Dockerfile | - | - | CPU таски (текст, PDF) |
| **db** | postgres:15 | - | 5432 | PostgreSQL |
| **redis** | redis:7 | - | 6379 | Celery broker + result backend |

---

## Повний pipeline персоналізації

```
Користувач                  Backend API              Celery Workers           S3 / ComfyUI
    |                          |                          |                       |
    |-- 1. Upload photo ------>|                          |                       |
    |   POST /upload_and_analyze/                         |                       |
    |                          |-- save photo ----------->|                       |--> child_photos/
    |                          |-- create Job (DB) ------>|                       |
    |                          |-- queue analyze -------->| analyze_photo_task    |
    |                          |                          |   (GPU queue)         |
    |                          |                          |-- Qwen2-VL analysis ->|
    |                          |                          |-- update Job.status   |
    |                          |                          |   = analyzing_completed|
    |<-- Personalization ------|                          |                       |
    |                          |                          |                       |
    |-- 2. Poll status ------->|                          |                       |
    |   GET /status/{id}       |                          |                       |
    |<-- status + analysis ----|                          |                       |
    |                          |                          |                       |
    |-- 3. Confirm + generate->|                          |                       |
    |   POST /generate/        |                          |                       |
    |   (child_name, child_age)|                          |                       |
    |                          |-- queue GPU task ------->| build_stage_backgrounds|
    |                          |                          |   (GPU queue)         |
    |                          |                          |                       |
    |                          |                          |-- For each page:      |
    |                          |                          |   if needs_face_swap: |
    |                          |                          |     upload to ComfyUI-|-> ComfyUI
    |                          |                          |     run workflow <----|-- result
    |                          |                          |     save bg -------->|--> layout/.../page_XX_bg.png
    |                          |                          |   else:              |
    |                          |                          |     copy base ------>|--> layout/.../page_XX_bg.png
    |                          |                          |                       |
    |                          |                          |-- queue CPU task ---->| render_stage_pages
    |                          |                          |                       |   (render queue)
    |                          |                          |                       |
    |                          |                          |   For each page:      |
    |                          |                          |     load bg from S3   |
    |                          |                          |     render text (Playwright)
    |                          |                          |     save final ------>|--> layout/.../page_XX.png
    |                          |                          |                       |
    |                          |                          |-- Job.status =        |
    |                          |                          |   prepay_ready        |
    |                          |                          |                       |
    |-- 4. View preview ------>|                          |                       |
    |   GET /preview/{id}      |                          |                       |
    |<-- presigned URLs -------|                          |                       |
    |                          |                          |                       |
    |-- 5. Add to cart ------->|                          |                       |
    |-- 6. Checkout + pay ---->|                          |                       |
    |                          |-- queue postpay -------->| (same pipeline for    |
    |                          |                          |  remaining pages)     |
    |                          |                          |-- Job.status =        |
    |                          |                          |   completed           |
    |                          |                          |                       |
    |-- 7. Download PDF ------>|                          |                       |
    |   GET /preview/{id}/download/pdf                    |                       |
    |<-- redirect to S3 URL ---|                          |                       |
```

---

## Стани Job (State Machine)

```
pending_analysis -----> analyzing -----> analyzing_completed
                                              |
                                              | POST /generate/
                                              v
                                        prepay_pending
                                              |
                                              | build_stage_backgrounds_task
                                              v
                                        prepay_generating
                                              |
                                              | render_stage_pages_task
                                              v
              +--- POST /regenerate/ -- prepay_ready
              |    (max 3 рази)             |
              +-----------------------------+
                                              |
                                              | Add to cart (confirmed)
                                              v
                                          confirmed
                                              |
                                              | Payment -> postpay generation
                                              v
                                        postpay_generating
                                              |
                                              v
                                          completed

Помилкові стани:
  analyzing ---------> analysis_failed
  *_generating ------> generation_failed
  будь-який ----------> cancelled (POST /cancel/)
```

---

## S3 структура зберігання

```
s3://bucket/
  |
  +-- child_photos/                    # Завантажені фото дітей
  |     +-- {job_id}_{filename}.jpg
  |
  +-- avatars/                         # Перезавантажені аватари
  |     +-- {job_id}_{filename}.jpg
  |
  +-- templates/{slug}/               # Шаблони книг (read-only)
  |     +-- manifest.json
  |     +-- pages/page_00_base.png
  |     +-- covers/front/base.jpg
  |     +-- fonts/Rubik-Regular.ttf
  |     +-- masks/mask_page_01.png     # Опційно
  |
  +-- layout/{job_id}/                 # Згенеровані сторінки
  |     +-- pages/
  |     |     +-- page_01_bg.png       # Після face swap (background)
  |     |     +-- page_01.png          # Фінальна сторінка (з текстом)
  |     |     +-- page_02_bg.png
  |     |     +-- page_02.png
  |     |     +-- ...
  |     +-- book.pdf                   # Lazy-generated PDF (кеш)
  |
  +-- results/{job_id}/               # Legacy формат (старий pipeline)
        +-- {illustration_id}.png
```

---

## Celery черги

| Черга | Worker | GPU | Таски |
|-------|--------|-----|-------|
| `gpu` | celery_worker | Yes | `analyze_photo_task`, `build_stage_backgrounds_task` |
| `render` | celery_render_worker | No | `render_stage_pages_task` |
| `celery` | обидва (fallback) | - | Дефолтна черга |

**Чому розділено:**
- GPU таски (face swap, аналіз) потребують відеокарту і є довготривалими (30-120 сек)
- CPU таски (рендеринг тексту, PDF) не потребують GPU і виконуються швидко
- Розділення дозволяє масштабувати воркери незалежно

---

## Технологічний стек

### Backend
- **Framework**: FastAPI (async Python)
- **ORM**: SQLAlchemy 2.0 (async)
- **DB**: PostgreSQL 15
- **Queue**: Celery 5.3 + Redis 7
- **Storage**: boto3 (S3-compatible: Yandex Cloud / MinIO)
- **Auth**: JWT (PyJWT) + bcrypt
- **ML**: Qwen2-VL (transformers), InsightFace, OpenCV
- **Rendering**: Playwright (HTML -> PNG)
- **Image Processing**: Pillow, OpenCV

### Frontend
- **Framework**: React 19 + TypeScript
- **Build**: Vite 7
- **Styling**: Tailwind CSS 4
- **Routing**: React Router 7
- **HTTP**: Native Fetch API
- **Deploy**: nginx (SPA routing + /api proxy)

### ML/AI Pipeline
- **Face Analysis**: Qwen2-VL-2B (vision-language model)
- **Face Transfer**: ComfyUI + Stable Diffusion + IPAdapter FaceID Plus V2
- **ControlNet**: Lineart + PiDiNet
- **Upscaling**: RealESRGAN x2
- **Face Detection**: OpenCV Haar Cascade, InsightFace

---

## Ключові файли проєкту

| Файл | Призначення |
|------|-------------|
| `backend/app/main.py` | FastAPI entry point, CORS, health checks |
| `backend/app/models.py` | SQLAlchemy ORM моделі (11 таблиць) |
| `backend/app/schemas.py` | Pydantic API схеми |
| `backend/app/config.py` | Settings з .env |
| `backend/app/auth.py` | JWT auth, bcrypt, dependency injectors |
| `backend/app/tasks.py` | Celery таски (analyze, face swap, render) |
| `backend/app/workers.py` | Celery app config, queue routing |
| `backend/app/inference/comfy_runner.py` | ComfyUI face transfer pipeline |
| `backend/app/inference/vision_qwen.py` | Qwen2-VL face analysis |
| `backend/app/rendering/html_text.py` | Playwright text layer rendering |
| `backend/app/book/manifest.py` | Book structure Pydantic models |
| `backend/app/book/manifest_store.py` | Load manifest from S3 |
| `backend/app/book/stages.py` | Prepay/postpay page selection |
| `backend/app/routes/personalizations.py` | Main API routes (upload, generate, preview, download) |
| `backend/app/routes/catalog.py` | Book catalog routes |
| `backend/app/routes/cart.py` | Shopping cart routes |
| `backend/app/routes/orders.py` | Order/checkout routes |
| `backend/app/routes/auth.py` | Auth routes (signup, login, etc.) |
| `backend/app/routes/account.py` | User profile routes |
| `docker-compose.yml` | Production Docker setup |
| `faceapp-front/src/pages/PersonalizationPage.tsx` | Upload + form UI |
| `faceapp-front/src/pages/PreviewPage.tsx` | Preview + polling UI |
