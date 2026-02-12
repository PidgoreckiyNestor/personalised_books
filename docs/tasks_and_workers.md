# Celery таски та воркери

**Файли:**
- `backend/app/tasks.py` — Визначення Celery тасків
- `backend/app/workers.py` — Конфігурація Celery app

---

## 1. Архітектура воркерів

```
Redis (broker)
  |
  +------ gpu queue -------> celery_worker (GPU)
  |                            analyze_photo_task
  |                            build_stage_backgrounds_task
  |
  +------ render queue -----> celery_render_worker (CPU)
  |                            render_stage_pages_task
  |
  +------ celery queue -----> (обидва воркери — fallback)
```

---

## 2. Конфігурація Celery (`workers.py`)

```python
celery_app = Celery("faceapp", broker=REDIS_URL, backend=REDIS_URL, include=["app.tasks"])
```

| Налаштування | Значення | Пояснення |
|-------------|---------|-----------|
| `task_track_started` | `True` | Дозволяє відстежувати стан "started" |
| `worker_prefetch_multiplier` | `1` | Воркер бере лише 1 таск за раз (важливо для GPU) |
| `task_acks_late` | `True` | Підтвердження лише після завершення (не на початку) |
| `visibility_timeout` | `3600` | 1 година — час після якого невідповідний таск стає видимим знову |

**Task routing:**

| Таск | Черга |
|------|-------|
| `analyze_photo_task` | `gpu` |
| `build_stage_backgrounds_task` | `gpu` |
| `render_stage_pages_task` | `render` |

---

## 3. Ланцюжок тасків (Pipeline)

```
POST /upload_and_analyze/
    |
    v
analyze_photo_task (GPU queue)
    |
    | Job.status = analyzing → analyzing_completed
    v
POST /generate/
    |
    v
build_stage_backgrounds_task (GPU queue)  ← якщо є face swap
    |
    | Job.status = prepay_generating
    | Автоматично ставить в чергу:
    v
render_stage_pages_task (render queue)
    |
    | Job.status = prepay_ready / completed
    v
Готово (або POST /generate/ для postpay)
```

**Альтернативний шлях:** Якщо stage **не** потребує face swap, `POST /generate/` запускає `render_stage_pages_task` напряму, пропускаючи GPU таск.

---

## 4. Допоміжні функції

### S3 операції

| Функція | Призначення |
|---------|-------------|
| `_s3_read_private_to_pil(s3_uri)` | Читає зображення з S3 → PIL Image. Підтримує формати: `s3://`, `http://`, відносний шлях |
| `_s3_write_pil(img, key, dpi)` | Записує PIL Image в S3 як PNG. Повертає `s3://bucket/key` |

### Ключі S3

| Функція | Результат |
|---------|-----------|
| `_layout_bg_key(job_id, 5)` | `layout/{job_id}/pages/page_05_bg.png` |
| `_layout_final_key(job_id, 5)` | `layout/{job_id}/pages/page_05.png` |

### Обробка зображень

| Функція | Призначення |
|---------|-------------|
| `_has_face(pil_img)` | Швидка перевірка наявності обличчя через OpenCV Haar Cascade |
| `_run_face_transfer(child, base, prompt, negative)` | Lazy-wrapper для `comfy_runner.run_face_transfer()` (імпорт лише при виклику) |

### БД операції

| Функція | Призначення |
|---------|-------------|
| `_get_job(db, job_id)` | Знайти Job по ID |
| `_upsert_artifact(db, ...)` | Створити запис `JobArtifact` для згенерованого файлу |

---

## 5. Таск `analyze_photo_task` (GPU)

**Що робить:** Аналізує фото дитини за допомогою Qwen2-VL (vision-language model) і генерує текстовий prompt для Stable Diffusion.

**Декоратори:**
```python
@celery_app.task(bind=True, acks_late=True, max_retries=3)
```
- `bind=True` — доступ до `self` (Celery task instance)
- `acks_late=True` — підтвердження після виконання
- `max_retries=3` — максимум 3 повторних спроби

**Аргументи:**
| Аргумент | Тип | Опис |
|---------|-----|------|
| `job_id` | str | UUID персоналізації |
| `child_photo_uri` | str | S3 URI фото (не використовується напряму — читає з Job) |
| `illustration_id` | str | Legacy параметр |
| `child_gender` | str | Legacy параметр |

**Потік виконання:**

```
1. Встановити job.status = "analyzing"
2. Прочитати фото з S3 → PIL Image
3. Запустити analyze_image_pil(pil, model_id)
   → Повертає JSON: {face_detected, gender, hair_color, hair_style, eye_color, skin_tone, prompt}
4. Зберегти результат в job.analysis_json
5. Побудувати common_prompt з аналізу:
   "child portrait, girl, dark brown hair, curly hairstyle, high quality"
6. Встановити job.status = "analyzing_completed"

При помилці:
   job.status = "analysis_failed"
```

---

## 6. Таск `build_stage_backgrounds_task` (GPU)

**Що робить:** Запускає face swap через ComfyUI для кожної сторінки stage. Генерує фонові зображення (без тексту).

**Декоратори:** `bind=True, acks_late=True, max_retries=2`

**Аргументи:**
| Аргумент | Тип | Опис |
|---------|-----|------|
| `job_id` | str | UUID персоналізації |
| `stage` | str | `"prepay"` або `"postpay"` |
| `randomize_seed` | bool | Рандомізувати seed (для перегенерації) |

**Потік виконання:**

```
1. Завантажити manifest книги
2. Визначити сторінки для stage (prepay_page_nums / page_nums_for_stage)
3. Визначити чи потрібно рандомізувати seed:
   - Якщо explicit=True → завжди
   - Якщо stage=prepay і analysis_json.generation_retry.randomize_seed=True → так
4. Встановити job.status = "prepay_generating" / "postpay_generating"
5. Прочитати фото дитини з S3

6. Для кожної сторінки:
   a. Якщо needs_face_swap=true:
      - Запустити _run_face_transfer(child, base_uri, prompt, negative)
      - ComfyUI виконує face swap через IPAdapter + ControlNet
   b. Якщо needs_face_swap=false:
      - Просто прочитати base image з S3

   c. Змінити розмір до output.page_size_px (якщо потрібно)
   d. Записати як layout/{job_id}/pages/page_XX_bg.png
   e. Створити запис JobArtifact (kind="page_bg_png")

7. Скинути randomize_seed flag в analysis_json
8. Поставити в чергу render_stage_pages_task (render queue)

При помилці:
   job.status = "generation_failed"
```

**Важливо:** Після завершення автоматично ставить `render_stage_pages_task` в чергу `render`:
```python
try:
    render_stage_pages_task.apply_async(args=(job_id, stage), queue="render")
except Exception:
    render_stage_pages_task.delay(job_id, stage)  # fallback
```

---

## 7. Таск `render_stage_pages_task` (CPU)

**Що робить:** Накладає текстові шари (ім'я дитини, вік тощо) поверх фонових зображень.

**Декоратори:** `bind=True, acks_late=True, max_retries=2`

**Аргументи:**
| Аргумент | Тип | Опис |
|---------|-----|------|
| `job_id` | str | UUID персоналізації |
| `stage` | str | `"prepay"` або `"postpay"` |

**Потік виконання:**

```
1. Завантажити manifest книги
2. Визначити сторінки для stage
3. Встановити job.status = "prepay_generating" / "postpay_generating"

4. Для кожної сторінки:
   a. Якщо needs_face_swap=true:
      - Прочитати bg з S3: layout/{job_id}/pages/page_XX_bg.png
   b. Якщо needs_face_swap=false:
      - Прочитати base image з S3 (оригінал шаблону)
      - Змінити розмір до output.page_size_px
      - Зберегти як _bg.png (артефакт для консистентності)

   c. Якщо є text_layers в manifest:
      - Викликати render_text_layers_over_image(bg_img, text_layers, template_vars, output_px)
      - template_vars = {child_name, child_age, child_gender}
   d. Якщо text_layers немає:
      - Використати bg_img як фінальне зображення

   e. Зберегти фінальне як layout/{job_id}/pages/page_XX.png
   f. Створити запис JobArtifact (kind="page_png")

5. Встановити фінальний статус:
   - prepay → job.status = "prepay_ready"
   - postpay → job.status = "completed"

При помилці:
   job.status = "generation_failed"
```

---

## 8. Таск `generate_image_task` (Legacy)

**Статус:** Legacy, зберігається для зворотної сумісності.

**Чим відрізняється від нового pipeline:**
- Використовує `illustrations.json` замість manifest
- Зберігає результати в `results/{job_id}/` замість `layout/{job_id}/`
- Не розділяє prepay/postpay stages
- Не накладає текстові шари
- Читає preview сторінки з таблиці `BookPreview`

---

## 9. Паттерн async-in-sync

Всі таски використовують один і той самий паттерн для запуску async коду всередині синхронного Celery таску:

```python
@celery_app.task(bind=True, acks_late=True)
def some_task(self, job_id: str):
    async def _run():
        async with AsyncSessionLocal() as db:
            # ... async код з SQLAlchemy, Playwright, etc.
            pass

    try:
        return asyncio.run(_run())
    except Exception as e:
        # Маркуємо job як failed
        async def _mark_failed():
            async with AsyncSessionLocal() as db:
                job = await _get_job(db, job_id)
                if job:
                    job.status = "generation_failed"
                    await db.commit()
        asyncio.run(_mark_failed())
        raise  # Re-raise для Celery retry
```

**Чому `asyncio.run()`?** — SQLAlchemy 2.0 з async працює через `AsyncSession`. Celery таски синхронні, тому `asyncio.run()` створює event loop для виконання async операцій.

---

## 10. Обробка помилок

| Ситуація | Поведінка |
|----------|-----------|
| Job не знайдено | `logger.error()`, return (без retry) |
| Face swap failed | `job.status = "generation_failed"`, re-raise для retry |
| S3 read/write failed | Exception propagated → task retry |
| Qwen2-VL analysis failed | `job.status = "analysis_failed"`, re-raise |
| Max retries exceeded | Celery маркує таск як failed |

**Важливо:** `acks_late=True` означає що якщо воркер "впаде" посеред таску, таск повернеться в чергу через `visibility_timeout` (1 година).

---

## 11. Залежності бібліотек

| Бібліотека | Використовується в |
|------------|-------------------|
| `celery` | Визначення тасків, routing |
| `boto3` | Читання/запис S3 |
| `PIL (Pillow)` | Обробка зображень |
| `cv2 (OpenCV)` | Face detection (Haar Cascade) |
| `numpy` | Конвертація PIL ↔ OpenCV |
| `sqlalchemy` (async) | Робота з БД |
| `asyncio` | Async-in-sync pattern |

**Lazy imports:**
- `comfy_runner.run_face_transfer` — імпортується лише при виклику face swap (щоб не завантажувати InsightFace на CPU воркері)
- `rendering.html_text.render_text_layers_over_image` — імпортується лише при рендерингу тексту
- `inference.vision_qwen.analyze_image_pil` — імпортується лише при аналізі фото
