# Повне пояснення `comfy_runner.py`

**Файл:** `backend/app/inference/comfy_runner.py`

## Context

Файл `comfy_runner.py` — це "міст" між Python-бекендом і ComfyUI сервером. Його єдина задача: взяти фото дитини + ілюстрацію книги → відправити на ComfyUI → отримати назад зображення де обличчя дитини "вставлене" в ілюстрацію.

---

## 1. Бібліотеки та їх призначення

```python
import io          # Робота з байтовими потоками в пам'яті (BytesIO) — щоб не писати файли на диск
import json        # Парсинг/серіалізація JSON (workflow файли, HTTP запити до ComfyUI)
import time        # time.time() для таймаутів, time.sleep() для polling
import uuid        # Генерація унікальних імен файлів (uuid4().hex) для upload на ComfyUI
import base64      # Кодування/декодування зображень в base64 (для утилітних функцій)
import requests    # HTTP клієнт — всі запити до ComfyUI REST API
import random      # Генерація випадкового seed для KSampler (randomize_seed)
from PIL import Image  # Pillow — робота з зображеннями (відкриття, конвертація, збереження)
from typing import Any, Dict, Optional  # Type hints
import os          # Робота з файловою системою (шляхи до workflow.json)
from ..config import settings  # Конфіг проекту (COMFY_BASE_URL, IPADAPTER_STRENGTH_SCALE)
from ..logger import logger    # Логування
```

**Додаткові (імпортуються лише при потребі, lazy imports):**
- `numpy` + `cv2` (OpenCV) — детекція обличчя на ілюстрації для створення маски
- `insightface` — локальний face swap як fallback (якщо ComfyUI недоступний)
- `boto3` — завантаження ілюстрацій з S3

---

## 2. Утилітні функції (рядки 14-24)

### `pil_to_base64()` / `base64_to_pil()`
Конвертація PIL Image ↔ base64 string. Використовуються для передачі зображень у текстовому форматі. В поточному коді ці функції **не викликаються** іншими функціями файлу — вони існують як утиліти "про запас".

---

## 3. `build_comfy_workflow()` — серце файлу (рядки 26-314)

### Що робить
Бере шаблон ComfyUI workflow (JSON) і підставляє в нього конкретні значення: які зображення завантажити, який промпт використати, який seed, і т.д.

### Параметри
| Параметр | Тип | Призначення |
|----------|-----|-------------|
| `child_photo_filename` | str | Ім'я файлу фото дитини (вже завантаженого на ComfyUI сервер) |
| `illustration_filename` | str | Ім'я файлу ілюстрації (вже завантаженого на ComfyUI сервер) |
| `prompt` | str | Позитивний промпт ("young girl, dark hair, big eyes") |
| `negative_prompt` | str | Негативний промпт (що НЕ генерувати) |
| `mask_filename` | Optional[str] | Ім'я файлу маски (де обличчя на ілюстрації) |
| `use_alpha_for_mask` | bool | Використовувати альфа-канал замість red channel (зараз завжди False) |
| `seed` | Optional[int] | Фіксований seed для відтворюваності результату |

### Три стратегії завантаження workflow (в порядку пріоритету)

#### Стратегія A: `workflow_api.json` (рядки 39-68)
**Найпростіший формат.** Це JSON експортований з ComfyUI через "Save (API format)". Кожен ключ — ID ноди, значення — `{class_type, inputs}`.

Алгоритм підстановки:
1. Знаходить всі ноди `LoadImage` → по підказці в імені файлу розуміє куди що підставити:
   - Ім'я містить "photo" → підставляє `child_photo_filename`
   - Ім'я містить "illustr" або "mask" → підставляє `illustration_filename`
2. Знаходить ноди `CLIPTextEncode` → підставляє промпти:
   - Якщо текст містить "girl"/"boy" або порожній → це позитивний промпт
   - Інакше → негативний промпт
3. Якщо передано `seed` → знаходить `KSampler` і встановлює seed

#### Стратегія B: `workflow.json` в API-форматі (рядки 83-120)
Якщо `workflow_api.json` не знайдено або зламався — пробує `workflow.json`. Перевіряє чи він в API-форматі (без "nodes" ключа, кожне значення має "class_type" + "inputs").

Підстановка по **захардкоженим ID нод**:
- Нода `"64"` → фото дитини
- Нода `"10"` → ілюстрація
- Нода `"150"` → маска

Також обробляє ControlNet loader (нормалізує шлях до моделі) і `ImageToMask` (вибір каналу).

#### Стратегія C: `workflow.json` в UI-форматі (рядки 122-314)
Найскладніший варіант. UI-формат — це те, що ComfyUI зберігає через "Save" (не API). Має структуру `{nodes: [...], links: [...]}`.

**Конвертація UI → API формат:**
1. Будує `prompt_dict` зі списку нод (рядки 122-131)
2. Розбирає `links` масив — відновлює зв'язки між нодами (рядки 133-142):
   - Кожен link = `[link_id, src_node, src_slot, dst_node, dst_slot, type]`
   - Перетворює в формат `inputs: {"name": [src_node_id, src_slot]}`
3. Підставляє значення для ~15 різних типів нод (рядки 144-313):

| Тип ноди | Що робить при обробці |
|----------|----------------------|
| `LoadImage` | Підставляє правильний файл (child/illustration/mask) по підказці в `widgets_values` |
| `CLIPTextEncode` | Підставляє промпти (нода 6 = positive, нода 19 = negative) |
| `CheckpointLoaderSimple` | Витягує ім'я checkpoint з widgets або ставить `dreamshaper_8.safetensors` |
| `KSampler` | Встановлює seed, steps (28), cfg (7.0), sampler (euler), scheduler (normal), denoise (1.0) |
| `ControlNetApplyAdvanced` | Strength (0.5), start/end percent |
| `ACN_ControlNetLoaderAdvanced` | Нормалізує шлях до ControlNet моделі (`control_v11p_sd15_lineart.pth`) |
| `IPAdapterUnifiedLoaderFaceID` | Preset (FACEID PLUS V2), lora_strength (0.4 * scale), provider (CUDA/CPU) |
| `IPAdapterFaceID` | Weight, weight_faceidv2, weight_type, combine_embeds, start/end, scaling — помножені на `IPADAPTER_STRENGTH_SCALE` |
| `ImageToMask` | Канал маски: "alpha" або "red" |
| `ImpactGaussianBlurMask` | Розмиття маски: kernel_size (10), sigma (8) |
| `InpaintModelConditioning` | noise_mask = True |
| `Image Crop Face` | Параметри кропу обличчя з фото дитини |
| `ImageHistogramMatch+` | Матчинг кольорів результату з оригінальною ілюстрацією |
| `PiDiNetPreprocessor` | Safe mode = enable |
| `Upscale Model Loader` | RealESRGAN_x2.pth |
| `ImageUpscaleWithModelBatched` | per_batch = 1 |
| `SaveImage` | Префікс імені файлу |

**Особлива логіка для IPAdapter (рядки 231-269):**
Значення `weight` і `weight_faceidv2` множаться на `IPADAPTER_STRENGTH_SCALE` з конфігу. Це дозволяє глобально регулювати "силу" face transfer без редагування workflow.

---

## 4. `upload_image_to_comfy()` (рядки 316-339)

Завантажує PIL Image на ComfyUI сервер через `POST /upload/image` (multipart form).

```
POST http://comfyui:8188/upload/image
  files: {"image": (filename, png_bytes, "image/png")}
  data: {"overwrite": "true"}
```

Повертає ім'я файлу на сервері (може відрізнятися від оригінального).

---

## 5. `_add_face_alpha_channel()` (рядки 341-411) — НЕ ВИКОРИСТОВУЄТЬСЯ

Створює RGBA зображення з альфа-каналом навколо обличчя. **Ця функція зараз не викликається** — код перейшов на explicit RGB маски через `_build_face_mask()` щоб уникнути помилки ComfyUI `"index 3 is out of bounds for dimension 3 with size 3"`.

Залишена в коді "про запас" на випадок якщо workflow зміниться.

---

## 6. `_build_face_mask()` (рядки 414-483) — ОСНОВНА генерація маски

### Призначення
Створює grayscale маску (mode="L") яка показує ComfyUI **де** на ілюстрації знаходиться обличчя. Біле = обличчя (замінювати), чорне = фон (не чіпати).

### Алгоритм
1. Конвертує зображення в grayscale
2. Запускає OpenCV Haar Cascade (`haarcascade_frontalface_default.xml`) для детекції обличчя
3. Якщо обличчя знайдено:
   - Обчислює центр і розміри еліпсу навколо обличчя (з відступами)
   - Малює білий еліпс на чорному фоні
4. Якщо обличчя НЕ знайдено (fallback):
   - Малює еліпс по центру верхньої половини зображення (припущення що обличчя зазвичай там)
5. Розмиває маску Gaussian blur (sigma пропорційний розміру зображення)

### Три рівні fallback
1. OpenCV + numpy → еліпс навколо детектованого обличчя
2. PIL ImageDraw → еліпс по центру (якщо OpenCV недоступний)
3. Повністю біла маска (якщо все зламалось — краще замінити все зображення ніж крашнутись)

---

## 7. `queue_prompt()` (рядки 485-497)

Відправляє підготовлений workflow на ComfyUI для виконання.

```
POST http://comfyui:8188/prompt
  body: {"prompt": <workflow_dict>}
  → response: {"prompt_id": "abc-123-..."}
```

Повертає `prompt_id` для подальшого polling.

---

## 8. `get_image_result()` (рядки 499-564) — Polling результату

### Алгоритм
1. Кожні 3 секунди робить `GET /history/{prompt_id}`
2. Чекає поки `status.completed == True`
3. Шукає результат в outputs:
   - Спочатку перевіряє **preferred ноди** `["140", "9"]` (140 = upscaled результат, 9 = звичайний SaveImage)
   - Якщо preferred не знайдено — бере першу ноду з images
4. Завантажує зображення через `GET /view?filename=...&type=output`
5. Таймаут: 300 секунд (5 хвилин)

### Чому "140" preferred
Нода 140 — це SaveImage після RealESRGAN upscale. Тобто код намагається спочатку взяти upscaled версію, а якщо її нема — звичайну.

---

## 9. `run_face_transfer_comfy_api()` (рядки 566-621) — Основний pipeline

**Це головна функція яку викликає продакшн код.** Оркеструє весь процес:

```
1. Якщо маска не передана → авто-генерація через _build_face_mask()
2. Завантаження 3 зображень на ComfyUI сервер:
   - child_{uuid}.png      → фото дитини
   - illustration_{uuid}.png → ілюстрація книги
   - mask_{uuid}.png        → маска обличчя (конвертована в RGB!)
3. Побудова workflow через build_comfy_workflow()
4. Відправка workflow → queue_prompt()
5. Очікування результату → get_image_result()
6. Повернення PIL Image
```

**Важливий момент (рядки 580-586):**
Маска завжди конвертується в RGB перед upload. Це тому що ComfyUI нода `ImageToMask(channel=red)` читає red channel з 3-канального зображення. Якщо передати grayscale — воно може крашнутись.

---

## 10. `run_face_transfer_local()` (рядки 623-694) — Fallback без ComfyUI

Альтернативна реалізація face swap через бібліотеку InsightFace (без Stable Diffusion, без ComfyUI).

### Алгоритм
1. Ініціалізує InsightFace з моделлю `buffalo_l` (детекція обличчя)
2. Детектує обличчя на обох зображеннях
3. Шукає модель `inswapper_128.onnx` (по декількох шляхах)
4. Виконує прямий face swap (source face → target face)

### Якість
Значно гірша ніж ComfyUI pipeline (немає ControlNet, немає inpainting, немає color matching). Використовується тільки як останній fallback.

---

## 11. `run_face_transfer()` (рядки 696-789) — Точка входу з S3

**Найвищий рівень абстракції.** Викликається з `tasks.py` (Celery worker).

### Що робить
1. Завантажує ілюстрацію з S3 (пробує `.png`, `.jpg`, `.jpeg`)
2. Шукає explicit маску в S3 за конвенцією `mask_<filename>.png`
3. Запускає `run_face_transfer_comfy_api()` (основний шлях)
4. Якщо ComfyUI впав → `run_face_transfer_local()` (fallback)
5. Якщо і fallback впав → повертає оригінальну ілюстрацію без змін

### Конвенція масок в S3
Для ілюстрації `pages/page_01_base.png` маска буде шукатись як `pages/mask_page_01_base.png`.

---

## 12. Загальна схема потоку даних

```
run_face_transfer(child_pil, s3_uri, prompt)          # Точка входу
  |
  +-- S3: завантажити ілюстрацію + шукати маску
  |
  +-- run_face_transfer_comfy_api(child, illust, mask)  # Основний шлях
  |     |
  |     +-- _build_face_mask(illust)  <-- якщо маска не знайдена в S3
  |     |     \-- OpenCV Haar Cascade -> еліпс -> blur
  |     |
  |     +-- upload_image_to_comfy() x 3  (child, illust, mask)
  |     |     \-- POST /upload/image
  |     |
  |     +-- build_comfy_workflow()  <-- підставити імена файлів в JSON
  |     |     \-- workflow_api.json -> patch LoadImage, CLIP, KSampler...
  |     |
  |     +-- queue_prompt()
  |     |     \-- POST /prompt -> prompt_id
  |     |
  |     \-- get_image_result()
  |           \-- GET /history/{id} (poll) -> GET /view (download)
  |
  \-- run_face_transfer_local(child, illust)            # Fallback
        \-- InsightFace inswapper_128.onnx
```

---

## 13. ComfyUI API ендпоінти що використовуються

| Метод | URL | Призначення |
|-------|-----|-------------|
| POST | `/upload/image` | Завантажити зображення на сервер |
| POST | `/prompt` | Запустити workflow на виконання |
| GET | `/history/{prompt_id}` | Перевірити статус виконання |
| GET | `/view?filename=...&type=output` | Завантажити результат |

---

## 14. Ключові рішення і "чому так"

1. **Чому explicit маска замість alpha-channel?** — ComfyUI `LoadImage` повертає 3-канальний тензор. `ImageToMask(channel=alpha)` крашиться з помилкою `"index 3 is out of bounds"`. Тому маска — окреме RGB зображення, і `ImageToMask` читає red channel.

2. **Чому три стратегії завантаження workflow?** — Історична причина. Спочатку був UI-формат, потім додали API-формат. Код підтримує обидва для backward compatibility.

3. **Чому `IPADAPTER_STRENGTH_SCALE`?** — Дозволяє глобально регулювати "силу" перенесення обличчя через env-змінну, без редагування workflow JSON.

4. **Чому fallback на InsightFace?** — Якщо ComfyUI сервер недоступний, краще повернути хоч щось ніж зламати весь pipeline книги.

5. **Чому preferred node IDs ["140", "9"]?** — Нода 140 = SaveImage після upscale (RealESRGAN x2), нода 9 = SaveImage без upscale. Код спочатку шукає якісніший результат.
