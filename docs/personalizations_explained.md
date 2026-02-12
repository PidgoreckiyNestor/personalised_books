# Повне пояснення `personalizations.py`

**Файл:** `backend/app/routes/personalizations.py`

## Context

Це головний файл API-роутів для персоналізації книг. Він обробляє весь життєвий цикл замовлення: від завантаження фото дитини до скачування готового PDF. По суті — це "контролер", який приймає HTTP запити від фронтенду і координує роботу Celery тасків, S3 сховища та бази даних.

---

## Структура файлу (ВАЖЛИВО)

Файл має **дві частини** з подвійними імпортами (рядки 1-371 і 372-1361). Це наслідок злиття двох файлів. Друга частина розширює першу додатковими ендпоінтами (PDF, ZIP, regenerate). Обидві частини створюють свій `router = APIRouter(...)` — FastAPI використовує обидва.

---

## 1. Бібліотеки та їх призначення

```python
# --- Стандартні ---
import json, os, uuid           # Генерація ID, робота з файлами
from datetime import datetime    # Таймстемпи для відповідей
from typing import List, Optional
from urllib.parse import urlparse  # Парсинг S3 URI та HTTP URL

# --- Веб-фреймворк ---
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse

# --- База даних ---
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# --- S3 ---
import boto3
from botocore.exceptions import ClientError  # Обробка S3 помилок (404, тощо)

# --- Async ---
from concurrent.futures import ThreadPoolExecutor  # PDF генерація в окремому потоці
import asyncio

# --- Зображення ---
from PIL import Image  # Для побудови PDF з PNG сторінок
import io, zipfile     # Створення ZIP/PDF в пам'яті

# --- Внутрішні модулі ---
from ..auth import get_current_user, get_current_user_optional, get_current_user_header_or_query, User
from ..book.manifest_store import load_manifest        # Завантаження структури книги
from ..book.stages import page_nums_for_front_preview, page_nums_for_stage, stage_has_face_swap
from ..config import settings
from ..db import get_db
from ..exceptions import InvalidJobStateError, JobNotFoundError, S3StorageError
from ..models import Book, BookPreview, Job
from ..schemas import Personalization, AvatarUploadResponse, PreviewResponse, PreviewPage, GenerationRetry
from ..tasks import analyze_photo_task, build_stage_backgrounds_task, render_stage_pages_task
```

---

## 2. Глобальні об'єкти

```python
router = APIRouter(tags=["Personalizations"])  # FastAPI роутер
GENERATION_RETRY_LIMIT = 3                     # Макс. кількість перегенерацій
_pdf_executor = ThreadPoolExecutor(max_workers=2)  # Пул потоків для PDF
s3 = boto3.client("s3", ...)                   # S3 клієнт (налаштований з .env)
```

---

## 3. Допоміжні функції (S3)

### `_s3_put_uploadfile(file, key)` — рядки 41-51, 424-436
Завантажує файл з HTTP запиту напряму в S3. Повертає `s3://bucket/key` URI.

### `_presigned_get(uri, expires=3600)` — рядки 54-107, 438-509
**Ключова функція.** Генерує presigned URL для доступу до приватних S3 об'єктів. Підтримує три формати вхідного URI:

| Формат входу | Приклад | Як обробляє |
|---|---|---|
| `s3://` | `s3://mybucket/photos/img.png` | Парсить bucket + key |
| `http(s)://` | `https://s3.example.com/mybucket/key` | Порівнює host з `AWS_ENDPOINT_URL`, парсить path-style або virtual-host |
| Відносний шлях | `templates/wonderland/page.jpg` | Використовує `S3_BUCKET_NAME` з конфігу |

Presigned URL діє 1 годину (3600 сек). Це дозволяє фронтенду завантажувати зображення напряму з S3 без бекенда.

### `_s3_get_bytes(bucket, key)` — рядок 573
Читає файл з S3 і повертає bytes. Використовується для PDF/ZIP генерації.

### `_layout_page_key(job_id, page_num)` — рядок 110
Конвенція іменування: `layout/{job_id}/pages/page_01.png`, `page_02.png`, ...

---

## 4. Допоміжні функції (Job/Preview)

### `_get_job_by_any_id(db, id)` — рядки 512-523
Шукає Job спочатку по `job_id`, потім по `cart_item_id`. Це потрібно тому що фронтенд може передати або UUID джоби, або ID елемента кошика.

### `_job_to_personalization(job, preview)` — рядки 170-201, 904-937
Конвертує ORM модель `Job` в Pydantic схему `Personalization` для API відповіді. Включає:
- Нормалізацію імені дитини (прибирає "unknown", пробіли)
- Генерацію presigned URL для аватара
- Інформацію про retry (used/limit/remaining/allowed)

### `_get_preview_for_job(job, db)` — рядки 127-167, 852-902
Збирає preview сторінки для відображення на фронтенді. **Два режими:**

1. **Manifest-driven (новий)** — читає `manifest.json` книги, визначає які сторінки показувати для prepay/postpay stage, генерує presigned URL з `layout/` папки в S3
2. **Legacy (BookPreview)** — fallback якщо маніфест відсутній, читає з таблиці `BookPreview` в БД

---

## 5. Retry-логіка для перегенерації

### Як зберігається
Дані retry зберігаються в `job.analysis_json` (JSONB поле в PostgreSQL):
```json
{
  "generation_retry": {
    "used": 1,
    "limit": 3,
    "randomize_seed": true
  }
}
```

### Функції
| Функція | Призначення |
|---------|-------------|
| `_read_generation_retry_used(job)` | Читає кількість використаних спроб |
| `_build_generation_retry(job)` | Будує об'єкт `GenerationRetry` для API відповіді |
| `_set_generation_retry_used(job, n)` | Записує кількість використаних спроб |
| `_set_generation_retry_randomize(job, bool)` | Вмикає/вимикає рандомізацію seed |

---

## 6. PDF/ZIP генерація

### `_build_pdf_bytes(job, page_nums)` — рядки 617-646
Завантажує всі PNG сторінки з S3, конвертує в RGB, і зшиває в один PDF через Pillow (`Image.save(format="PDF", save_all=True)`). Виконується в `ThreadPoolExecutor` щоб не блокувати async event loop.

### `_ensure_pdf_in_s3(job, page_nums)` — рядки 649-679
Lazy-генерація PDF: спочатку перевіряє чи PDF вже існує в S3 (`HEAD` запит), якщо ні — генерує і зберігає. Після запису чекає поки S3 "побачить" файл (eventual consistency).

### `_wait_for_s3_object(bucket, key)` — рядки 602-614
Polling S3 HEAD запитом (до 6 спроб, кожні 0.5 сек) щоб переконатись що файл доступний після запису.

---

## 7. API Ендпоінти

### Діаграма стану Job

```
pending_analysis
    |  (analyze_photo_task)
    v
analyzing
    |
    v
analyzing_completed
    |  (POST /generate/)
    v
prepay_pending
    |  (build_stage_backgrounds_task -> render_stage_pages_task)
    v
prepay_generating
    |
    v
prepay_ready  <---+
    |              |  (POST /regenerate/ — до 3 разів)
    v              |
confirmed ---------+
    |  (оплата -> postpay generation)
    v
postpay_generating
    |
    v
completed
```

Окремі стани: `generation_failed`, `analysis_failed`, `cancelled`

---

### `POST /upload_and_analyze/` — Крок 1: Завантаження фото

**Що робить:**
1. Перевіряє що книга (`slug`) існує в БД
2. Валідує тип файлу (тільки jpg/png)
3. Завантажує фото в S3: `child_photos/{job_id}_{filename}`
4. Створює запис `Job` в БД зі статусом `pending_analysis`
5. Запускає Celery таск `analyze_photo_task` в GPU черзі

**Вхід:** `multipart/form-data` з полями `slug` + `child_photo`
**Вихід:** `Personalization` JSON

---

### `POST /generate/` — Крок 2: Підтвердження і старт генерації

**Що робить:**
1. Знаходить Job, перевіряє статус (`analyzing_completed` або `prepay_ready`)
2. Оновлює `child_name` і `child_age` якщо передані
3. Ставить статус `prepay_pending`
4. Визначає чи потрібен face swap для prepay stage:
   - **Так** → `build_stage_backgrounds_task` (GPU черга)
   - **Ні** → `render_stage_pages_task` (CPU черга)

**Вхід:** `multipart/form-data` з `job_id`, опційно `child_name`, `child_age`
**Вихід:** `{"status": "ok", "message": "Generation started"}`

**Примітка:** Auth check закоментований для тестування (рядки 343-347).

---

### `GET /status/{job_id}` — Перевірка статусу

Повертає поточний стан Job + preview (якщо є). Фронтенд полить цей ендпоінт кожні кілька секунд.

---

### `GET /preview/{job_id}?stage=prepay|postpay` — Preview сторінок

Повертає список сторінок з presigned URL для відображення. Підтримує пошук як по `job_id`, так і по `cart_item_id`.

**Доступність:**
- `stage=prepay` — потрібен статус `prepay_ready` або пізніше
- `stage=postpay` — тільки `completed`

---

### `GET /result/{job_id}` — Legacy preview (fallback)

Старий ендпоінт для preview. Спочатку пробує manifest-driven підхід, якщо не працює — шукає згенеровані зображення в `results/{job_id}/` на S3 і підставляє їх замість оригінальних ілюстрацій.

---

### `POST /avatar/{job_id}` — Заміна фото

Дозволяє замінити фото дитини. Після заміни:
1. Оновлює `child_photo_uri` і `avatar_url`
2. Скидає статус на `pending_analysis`
3. Перезапускає `analyze_photo_task`

---

### `POST /regenerate/{job_id}` — Перегенерація (до 3 разів)

Перезапускає prepay генерацію з **рандомним seed** (щоб отримати інший результат face swap).

**Обмеження:**
- Максимум 3 спроби (`GENERATION_RETRY_LIMIT = 3`)
- Потрібна авторизація + перевірка що Job належить користувачу
- Дозволені статуси: `generation_failed`, `prepay_ready`, `confirmed`, `preview_ready`

---

### `POST /cancel/{job_id}` — Скасування

Просто ставить `job.status = "cancelled"`. Не зупиняє вже запущені Celery таски.

---

### `GET /jobs` — Список всіх персоналізацій

Повертає всі Job'и поточного користувача з preview для кожного. Потрібна авторизація.

---

### Download ендпоінти (тільки для `completed` Jobs, тільки для власника)

| Ендпоінт | Формат | Як працює |
|----------|--------|-----------|
| `GET /preview/{job_id}/download/page/{page_num}` | PNG | Читає одну сторінку з S3, повертає як attachment |
| `GET /preview/{job_id}/download/zip` | ZIP | Збирає всі сторінки в ZIP через `StreamingResponse` (чанками по 1MB) |
| `GET /preview/{job_id}/download/pdf` | PDF | Генерує/кешує PDF в S3, redirect на presigned URL |
| `GET /preview/{job_id}/download/pdf-url` | JSON | Повертає presigned URL для PDF (для фронтенду) |

**PDF кешування:** PDF генерується один раз і зберігається в `layout/{job_id}/book.pdf`. Наступні запити отримують його напряму з S3.

---

## 8. Паттерн dispatch Celery тасків

По всьому файлу використовується один і той самий паттерн:

```python
try:
    task.apply_async(args=(...), queue="gpu")  # Спочатку з явною чергою
except Exception:
    task.delay(...)  # Fallback без вказання черги (дефолтна)
```

Це захист від ситуації коли Redis (Celery broker) тимчасово недоступний для `apply_async` з розширеними параметрами.

---

## 9. Схема потоку даних

```
Frontend                    Backend API                     Celery Workers        S3
   |                           |                                |                 |
   |--- POST /upload_and_analyze/ -->                           |                 |
   |                           |--- upload photo ------------->  |                 |--> child_photos/
   |                           |--- create Job (DB) ----------> |                 |
   |                           |--- analyze_photo_task -------> |  (GPU queue)    |
   |                           |                                |--- Qwen2-VL --> |
   |                           |                                |--- update Job   |
   |<-- Personalization JSON --|                                |                 |
   |                           |                                |                 |
   |--- POST /generate/ ------>|                                |                 |
   |                           |--- build_stage_backgrounds --> |  (GPU queue)    |
   |                           |                                |--- ComfyUI -->  |--> layout/.../page_XX_bg.png
   |                           |                                |--- render  -->  |--> layout/.../page_XX.png
   |                           |                                |--- status=prepay_ready
   |<-- {"status": "ok"} -----|                                |                 |
   |                           |                                |                 |
   |--- GET /status/{id} ----->|                                |                 |
   |<-- status + preview URLs -|                                |                 |
   |                           |                                |                 |
   |--- GET /preview/{id}/download/pdf -->                      |                 |
   |                           |--- _ensure_pdf_in_s3 -------->  |                 |--> layout/.../book.pdf
   |<-- redirect to S3 URL ---|                                |                 |
```

---

## 10. Ключові рішення і "чому так"

1. **Чому два блоки імпортів?** — Історичний артефакт злиття файлів. Перша частина — базовий CRUD, друга — розширені функції (downloads, retry). Працює бо FastAPI реєструє обидва роутери.

2. **Чому `_presigned_get` така складна?** — URI приходять в різних форматах (s3://, http, відносний шлях) з різних частин системи. Функція уніфікує їх в presigned URL.

3. **Чому PDF генерується lazy?** — PDF потрібен тільки для скачування, не для preview. Генерація важка (читання ~30 PNG з S3 + конвертація). Кешування в S3 уникає повторної роботи.

4. **Чому `_wait_for_s3_object` після запису?** — S3 має eventual consistency. Одразу після `put_object` файл може бути ще "не видимий" для `get_object`. Polling через `head_object` дає час на propagation.

5. **Чому auth check закоментований в `/generate/`?** — Для спрощення тестування. В продакшні потрібно розкоментувати.

6. **Чому `try/except` навколо `apply_async`?** — Якщо Redis broker тимчасово недоступний, `apply_async` з параметром `queue=` може впасти, а простий `delay()` використає default queue і може пройти.
