# Інфраструктура та деплой

**Файли:**
- `docker-compose.yml` — Production Docker setup
- `backend/Dockerfile` — Backend image (CUDA)
- `comfyui/Dockerfile` — ComfyUI image
- `faceapp-front/Dockerfile` — Frontend image (nginx)
- `backend/app/config.py` — Settings (Pydantic BaseSettings)

---

## 1. Docker Compose — 7 сервісів

```
                     Frontend (nginx:80)
                          |
                          | /api/* proxy
                          v
                       Web (FastAPI:8000)
                      /    |    \
                     /     |     \
                    v      v      v
              DB (5432) Redis(6379) S3 (Yandex/MinIO)
                           |
              +------------+------------+
              |                         |
              v                         v
       celery_worker (GPU)    celery_render_worker (CPU)
              |
              v
        ComfyUI (8188)
```

### Огляд сервісів

| Сервіс | Image | GPU | Port | Призначення |
|--------|-------|-----|------|-------------|
| `db` | postgres:15-alpine | - | 5432 | PostgreSQL БД |
| `redis` | redis:7-alpine | - | 6379 | Celery broker + result backend |
| `web` | backend/Dockerfile | - | 8000 | FastAPI API сервер |
| `comfyui` | comfyui/Dockerfile | Yes | 8188 | Stable Diffusion + face swap |
| `celery_worker` | backend/Dockerfile | Yes | - | GPU таски (аналіз, face swap) |
| `celery_render_worker` | backend/Dockerfile | - | - | CPU таски (текст, PDF) |
| `frontend` | faceapp-front/Dockerfile | - | 80 | React SPA (nginx) |

---

## 2. Docker Volumes

| Volume | Де монтується | Призначення |
|--------|---------------|-------------|
| `postgres_data` | `/var/lib/postgresql/data/` | Дані PostgreSQL |
| `models_cache` | `/models` (backend), `/home/runner/ComfyUI/models` (comfyui) | Кеш ML моделей (HuggingFace, ControlNet, тощо) |
| `comfyui_data` | `/home/runner/ComfyUI` | ComfyUI workspace |

**`models_cache` — спільний volume** між `web`, `celery_worker`, `celery_render_worker` і `comfyui`. Дозволяє завантажити ML моделі один раз і використовувати скрізь.

---

## 3. Dockerfiles

### 3.1 Backend (`backend/Dockerfile`)

```dockerfile
FROM nvidia/cuda:11.7.1-devel-ubuntu20.04

# Python 3 + OpenCV deps
RUN apt-get install python3 python3-pip libglib2.0-0 libgl1 build-essential

# Python deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# Playwright (для HTML→PNG text rendering)
RUN python -m playwright install --with-deps chromium

COPY . .
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Базовий образ:** NVIDIA CUDA 11.7 (для GPU inference)
**Playwright:** Встановлюється Chromium для рендерингу тексту на сторінках

### 3.2 ComfyUI (`comfyui/Dockerfile`)

```dockerfile
FROM python:3.11-slim

# Git clone ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI.git

# Custom nodes + models
RUN pip install opencv-python onnxruntime-gpu controlnet-aux einops timm
```

**Proxy support:** Підтримує SOCKS/HTTP proxy для build та runtime (для обмежених мереж).

### 3.3 Frontend (`faceapp-front/Dockerfile`)

```dockerfile
FROM node:22-alpine AS build
# npm install + npm run build

FROM nginx:1.25-alpine
# Copy build output + nginx.conf
```

**Multi-stage build:** Node.js для збірки → nginx для serving.
**`VITE_API_BASE_URL`** передається як build arg для Vite.

---

## 4. Конфігурація (`config.py`)

Всі налаштування через `pydantic-settings` з підтримкою `.env` файлу:

| Змінна | Default | Опис |
|--------|---------|------|
| `DATABASE_URL` | `postgresql+psycopg://user:password@db/dbname` | PostgreSQL connection string |
| `AWS_ENDPOINT_URL` | (required) | S3 endpoint (Yandex Cloud / MinIO) |
| `AWS_ACCESS_KEY_ID` | (required) | S3 access key |
| `AWS_SECRET_ACCESS_KEY` | (required) | S3 secret key |
| `AWS_REGION_NAME` | `ru-1` | S3 region |
| `S3_BUCKET_NAME` | (required) | S3 bucket name |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis URL для Celery |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Redis URL для результатів |
| `HF_HOME` | `/models/hf` | HuggingFace cache directory |
| `QWEN_MODEL_ID` | `Qwen/Qwen2-VL-2B-Instruct` | ID моделі для аналізу фото |
| `COMFY_BASE_URL` | `http://127.0.0.1:8188` | URL ComfyUI сервера |
| `IPADAPTER_STRENGTH_SCALE` | `1.0` | Масштаб IPAdapter face swap |
| `JWT_SECRET_KEY` | (hardcoded default) | Секрет для JWT |
| `JWT_ALGORITHM` | `HS256` | Алгоритм JWT |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 днів) | Час дії JWT |

---

## 5. Health Checks

| Сервіс | Перевірка | Інтервал |
|--------|-----------|---------|
| `db` | `pg_isready -U user -d dbname` | 5s |
| `redis` | `redis-cli PING` | 5s |
| `frontend` | `wget --spider http://127.0.0.1:80` | 30s |

**Залежності:**
- `web` чекає на `db` + `redis` (service_healthy)
- `celery_worker` чекає на `db` + `redis` + `comfyui` (service_started)
- `celery_render_worker` чекає на `db` + `redis`
- `frontend` чекає на `web` (service_started)

---

## 6. Celery Workers — команди запуску

```bash
# GPU worker: черги gpu + celery (fallback)
python -m celery -A app.workers.celery_app worker \
    --loglevel=info --concurrency=1 -Q gpu,celery

# CPU render worker: черги render + celery (fallback)
python -m celery -A app.workers.celery_app worker \
    --loglevel=info --concurrency=1 -Q render,celery
```

**`--concurrency=1`** — тільки один таск одночасно (важливо для GPU — один процес = одна GPU).

---

## 7. GPU конфігурація

GPU доступний для `comfyui` і `celery_worker`:

```yaml
deploy:
  resources:
    reservations:
      devices:
        - capabilities: ["gpu"]
```

**`celery_render_worker`** — працює **без GPU** (тільки CPU таски: text rendering, PDF).

---

## 8. Особливі env змінні

### ComfyUI Proxy

| Змінна | Опис |
|--------|------|
| `COMFY_BUILD_PROXY` | Proxy для build (apt, git, pip) |
| `COMFY_PROXY` | Proxy для runtime (HuggingFace downloads) |
| `COMFY_PROXY_ENABLED` | `1`/`0` — ввімкнути/вимкнути proxy |
| `COMFY_NO_PROXY` | Виключення з proxy (localhost, внутрішні сервіси) |

### InsightFace

```yaml
volumes:
  - ./models/insightface:/models/insightface:ro
environment:
  INSIGHTFACE_HOME: "/models/insightface"
```

InsightFace моделі монтуються read-only з хоста.

---

## 9. Локальна розробка

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd faceapp-front
npm install
npm run dev
```

### Docker (часткове)

```bash
# Тільки інфраструктура (без worker'ів)
docker compose up -d db redis

# Тільки web + frontend
docker compose up -d web frontend

# Все
docker compose up -d
```

---

## 10. Порядок старту

```
1. db (PostgreSQL)        — ініціалізація БД
2. redis                  — broker для Celery
3. comfyui                — завантаження ML моделей (може тривати 1-5 хв)
4. web (FastAPI)          — create_all() створює таблиці
5. celery_worker (GPU)    — підключається до Redis + ComfyUI
6. celery_render_worker   — підключається до Redis
7. frontend (nginx)       — serving SPA
```

**`create_all()`** — FastAPI при старті автоматично створює всі таблиці через `Base.metadata.create_all()`. Немає Alembic міграцій.
