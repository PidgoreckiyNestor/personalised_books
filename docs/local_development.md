# Локальна розробка — повний гайд

---

## 1. Загальна стратегія: що запускати локально, а що ні

Не потрібно запускати **все** локально. Проєкт складається з компонентів різної "ваги":

```
Легкі (завжди локально)          Важкі (опціонально)
─────────────────────            ──────────────────────
PostgreSQL (Docker)              ComfyUI + GPU моделі (~10 GB)
Redis (Docker)                   Qwen2-VL (~4 GB)
MinIO / S3 (Docker)              InsightFace (~500 MB)
FastAPI backend (Python)
Celery worker (Python)
Frontend (npm run dev)
```

### Рекомендовані режими розробки

| Що робиш | Що запускати | GPU потрібен? |
|----------|-------------|---------------|
| **Frontend** | DB + Redis + MinIO + Backend API | Ні |
| **Backend API / роути** | DB + Redis + MinIO | Ні |
| **Text rendering** | DB + Redis + MinIO + Celery render worker | Ні |
| **Face swap pipeline** | Все + ComfyUI | Так (або Colab) |
| **Повний E2E тест** | Все | Так (або Colab) |

---

## 2. Варіант А: Mac без GPU (рекомендований для початку)

Це найпростіший варіант — працює на будь-якому Mac (Intel або Apple Silicon).
Face swap не працюватиме, але все інше — так.

### Крок 1: Запуск інфраструктури

```bash
# З кореня проєкту
docker compose -f docker-compose.local.yml up -d
```

Це запустить:
- **PostgreSQL** — `localhost:5432` (user: books / password: books)
- **Redis** — `localhost:6379`
- **MinIO** — `localhost:9000` (S3 API), `localhost:9001` (Web Console)
- **MinIO init** — автоматично створить bucket `personalized-books`

ComfyUI **не** запуститься без NVIDIA GPU — це нормально.

### Крок 2: Налаштування MinIO

```bash
# Встановити MinIO Client (якщо не встановлено)
brew install minio/stable/mc

# Завантажити шаблони книг в MinIO
bash scripts/setup_minio.sh
```

Після цього відкрий http://localhost:9001 (minioadmin / minioadmin) і перевір що bucket `personalized-books` створено і в ньому є `templates/`.

### Крок 3: Налаштування backend .env

```bash
cp backend/.env.local backend/.env
```

Файл `.env.local` вже містить правильні значення для локальної розробки:

```env
DATABASE_URL=postgresql+asyncpg://books:books@localhost:5432/books
AWS_ENDPOINT_URL=http://localhost:9000
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
S3_BUCKET_NAME=personalized-books
COMFY_BASE_URL=http://localhost:8188
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

**Важливо:** Перевір що `DATABASE_URL` використовує `asyncpg` (для локального запуску), а не `psycopg` (для Docker).

### Крок 4: Python environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Встановити Playwright (для text rendering)
python -m playwright install chromium
```

**Примітка:** `torch`, `transformers`, `insightface` — великі пакети (~5 GB). Якщо не плануєш працювати з ML, можна закоментувати їх в requirements.txt.

### Крок 5: Запуск Backend API

```bash
cd backend
source venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

При першому запуску FastAPI автоматично створить всі таблиці в PostgreSQL.

Перевір: http://localhost:8000/docs — Swagger UI

### Крок 6: Запуск Celery Worker (окремий термінал)

```bash
cd backend
source venv/bin/activate

# Для роботи без GPU (тільки text rendering)
python -m celery -A app.workers.celery_app worker --loglevel=info -Q render,celery
```

Для повного pipeline (з face swap):
```bash
python -m celery -A app.workers.celery_app worker --loglevel=info -Q gpu,render,celery
```

### Крок 7: Запуск Frontend (окремий термінал)

```bash
cd faceapp-front
npm install
npm run dev
```

Frontend буде на http://localhost:5173

### Крок 8: Seed Data (опціонально)

Щоб мати книги в каталозі, потрібно заповнити БД:

```bash
cd backend
source venv/bin/activate
python -c "
import asyncio
from app.db import engine, AsyncSessionLocal
from app.models import Base
from app.seed_data import seed_books

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        await seed_books(db)
        await db.commit()

asyncio.run(main())
"
```

---

## 3. Варіант Б: Mac + ComfyUI через Google Colab

Якщо потрібен face swap, але немає GPU:

### Крок 1-7: Як у Варіанті А

### Крок 8: Запуск ComfyUI в Colab

1. Використай Colab notebook з GPU runtime (T4 або краще)
2. Встанови ComfyUI + ngrok tunnel
3. Отримай публічний URL (напр. `https://abc123.ngrok.io`)

### Крок 9: Оновити .env

```env
COMFY_BASE_URL=https://abc123.ngrok.io
```

Перезапусти backend і celery worker.

---

## 4. Варіант В: Linux з NVIDIA GPU (повний pipeline)

```bash
# Запуск ВСЬОГО через Docker Compose
docker compose up -d

# Або з dev overrides (hot-reload)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Це запустить всі 7 сервісів включно з ComfyUI.

---

## 5. Структура терміналів при локальній розробці

```
Terminal 1: Docker (інфраструктура)
$ docker compose -f docker-compose.local.yml up -d
$ docker compose -f docker-compose.local.yml logs -f    # слідкувати за логами

Terminal 2: Backend API
$ cd backend && source venv/bin/activate
$ python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Terminal 3: Celery Worker
$ cd backend && source venv/bin/activate
$ python -m celery -A app.workers.celery_app worker --loglevel=info -Q render,celery

Terminal 4: Frontend
$ cd faceapp-front && npm run dev
```

---

## 6. Порти та URL

| Сервіс | URL | Опис |
|--------|-----|------|
| **Backend API** | http://localhost:8000 | FastAPI |
| **Swagger UI** | http://localhost:8000/docs | API документація |
| **Frontend** | http://localhost:5173 | React dev server |
| **PostgreSQL** | localhost:5432 | books/books/books |
| **Redis** | localhost:6379 | — |
| **MinIO S3 API** | http://localhost:9000 | S3 API |
| **MinIO Console** | http://localhost:9001 | Web UI (minioadmin/minioadmin) |
| **ComfyUI** | http://localhost:8188 | Якщо запущено |

---

## 7. Робочі процеси (workflows)

### 7.1 Розробка нового API ендпоінту

1. Запусти DB + Redis + MinIO + Backend
2. Додай/зміни роут в `backend/app/routes/`
3. Backend перезавантажиться автоматично (`--reload`)
4. Тестуй через Swagger UI: http://localhost:8000/docs
5. Або через `curl` / Postman

### 7.2 Робота з текстовими шарами (text rendering)

1. Запусти DB + Redis + MinIO + Backend + Celery (render queue)
2. Зміни текст/стилі в `backend/templates/{slug}/manifest.json`
3. Перезавантаж templates в MinIO: `bash scripts/setup_minio.sh`
4. Запусти генерацію через API або тест:
   ```bash
   cd backend
   python -c "
   import asyncio
   from app.rendering.html_text import render_text_layers_over_image
   from app.book.manifest_store import load_manifest
   from PIL import Image

   async def test():
       manifest = load_manifest('wonderland-book')
       page = manifest.page_by_num(0)
       bg = Image.new('RGB', (1080, 1080), (50, 50, 80))
       result = await render_text_layers_over_image(
           bg, page.text_layers,
           template_vars={'child_name': 'Аліса', 'child_age': 5, 'child_gender': 'girl'},
           output_px=1080
       )
       result.save('test_render.png')
       print('Saved test_render.png')

   asyncio.run(test())
   "
   ```

### 7.3 Робота з маніфестом книги

1. Відредагуй `backend/templates/{slug}/manifest.json`
2. Оновити в MinIO:
   ```bash
   mc cp backend/templates/wonderland-book/manifest.json \
        local/personalized-books/templates/wonderland-book/manifest.json
   ```
3. Тестуй (manifest перезавантажується при кожному запиті — без кешування)

### 7.4 Робота з face swap

1. Потрібен ComfyUI (локально або через Colab)
2. Тестовий скрипт:
   ```bash
   cd backend
   python test_face_swap.py --child-photo path/to/photo.jpg --illustration path/to/illustration.png
   ```

### 7.5 Робота з Frontend

1. Запусти Backend API на :8000
2. `cd faceapp-front && npm run dev`
3. Vite проксує `/api/*` на backend (перевір `vite.config.ts`)
4. Hot-reload працює автоматично

---

## 8. Як дебажити

### Backend

```bash
# Логи API
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

# Логи Celery
python -m celery -A app.workers.celery_app worker --loglevel=debug -Q render,celery
```

### PostgreSQL

```bash
# Підключитись до БД
docker exec -it $(docker ps -qf "ancestor=postgres:15-alpine") psql -U books -d books

# Корисні запити
SELECT job_id, status, child_name, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;
SELECT * FROM books;
SELECT * FROM cart_items;
```

### MinIO (S3)

```bash
# Список файлів
mc ls local/personalized-books/templates/

# Переглянути конкретний файл
mc cat local/personalized-books/templates/wonderland-book/manifest.json

# Видалити layout для перегенерації
mc rm --recursive local/personalized-books/layout/{job_id}/
```

### Redis (Celery)

```bash
# Перевірити черги
docker exec -it $(docker ps -qf "ancestor=redis:7-alpine") redis-cli

# В redis-cli:
KEYS *
LLEN gpu       # Кількість тасків в GPU черзі
LLEN render    # Кількість тасків в render черзі
```

---

## 9. Типові проблеми

### "Connection refused" до PostgreSQL

**Причина:** asyncpg vs psycopg driver.
- Для локального запуску: `DATABASE_URL=postgresql+asyncpg://...`
- Для Docker: `DATABASE_URL=postgresql+psycopg://...`

### MinIO "bucket not found"

```bash
# Перевір що bucket існує
mc ls local/

# Створи вручну якщо потрібно
mc mb local/personalized-books
mc anonymous set download local/personalized-books
```

### Playwright "browser not found"

```bash
python -m playwright install chromium
# Якщо помилка з deps:
python -m playwright install-deps chromium
```

### Celery worker не бачить таски

Перевір що:
1. Redis запущено: `redis-cli PING` → `PONG`
2. `CELERY_BROKER_URL` в `.env` вказує на правильний Redis
3. Worker запущено з правильними чергами: `-Q render,celery` або `-Q gpu,render,celery`

### "No module named 'app'"

Переконайся що запускаєш з директорії `backend/`:
```bash
cd backend
python -m uvicorn app.main:app ...
```

### torch / CUDA помилки на Mac

На Mac немає CUDA. Qwen2-VL використає MPS (Apple Silicon) або CPU. Це нормально для розробки, але повільно. Для face swap потрібен ComfyUI з GPU (Colab або Linux).

---

## 10. Швидкий старт (copy-paste)

```bash
# 1. Інфраструктура
docker compose -f docker-compose.local.yml up -d

# 2. MinIO setup
brew install minio/stable/mc   # якщо не встановлено
bash scripts/setup_minio.sh

# 3. Backend
cd backend
cp .env.local .env
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# 4. Запуск (3 термінали)

# Terminal 1: API
cd backend && source venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Celery
cd backend && source venv/bin/activate
python -m celery -A app.workers.celery_app worker --loglevel=info -Q render,celery

# Terminal 3: Frontend
cd faceapp-front && npm install && npm run dev
```

**Готово!**
- API: http://localhost:8000/docs
- Frontend: http://localhost:5173
- MinIO: http://localhost:9001

---

## 11. Зупинка всього

```bash
# Зупинити Docker інфраструктуру
docker compose -f docker-compose.local.yml down

# Зупинити зі збереженням даних (volumes)
docker compose -f docker-compose.local.yml down

# Зупинити з видаленням даних
docker compose -f docker-compose.local.yml down -v
```

Backend та Celery зупиняються через `Ctrl+C` в відповідних терміналах.
