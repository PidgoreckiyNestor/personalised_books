# ML Inference та рендеринг тексту

**Файли:**
- `backend/app/rendering/html_text.py` — Playwright-based text rendering pipeline
- `backend/app/inference/vision_qwen.py` — Qwen2-VL аналіз фото дитини

---

## 1. Text Rendering Pipeline (`html_text.py`)

### 1.1 Концепція

Текст накладається поверх ілюстрацій через **HTML → PNG** конвертацію:

```
PIL Image (bg)        TextLayer (manifest)
     |                      |
     v                      v
base64 data URI      template rendering ({child_name} → "Аліса")
     |                      |
     +----------+-----------+
                |
                v
        HTML document (CSS styled text over bg image)
                |
                v
        Playwright (headless Chromium)
                |
                v
        Screenshot → PIL Image (final)
```

**Чому HTML/CSS а не PIL text?** — CSS дає точне управління шрифтами, тінями, обведенням, вирівнюванням, HTML-тегами (`<span class="bold">`), і гарантує ідентичний рендеринг на всіх платформах.

### 1.2 Головна функція

```python
async def render_text_layers_over_image(
    bg_img: Image.Image,       # Фонове зображення
    layers: List[TextLayer],    # Текстові шари з маніфесту
    template_vars: Dict,        # {child_name: "Аліса", child_age: 5, child_gender: "girl"}
    output_px: int,             # Розмір виходу (1080 або 2551)
) -> Image.Image
```

**Алгоритм:**
1. Запустити headless Chromium через Playwright
2. Для кожного TextLayer:
   a. Підставити змінні в шаблон: `{child_name}` → `"Аліса"`
   b. Конвертувати поточне зображення в base64 data URI
   c. Завантажити шрифт з S3 → base64 data URI
   d. Побудувати HTML документ (bg + styled text)
   e. Завантажити в Playwright, зробити screenshot
   f. Результат стає фоном для наступного шару
3. Закрити браузер, повернути фінальне зображення

### 1.3 HTML документ

Генерується функцією `_build_html()`. Структура:

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @font-face {
    font-family: 'CustomFont';
    src: url('data:font/ttf;base64,...');  /* шрифт з S3 */
  }

  body {
    width: 1080px; height: 1080px;
    background: url('data:image/png;base64,...');  /* фон */
  }

  .text {
    position: relative;
    margin-top: 630px;       /* top */
    margin-left: 129px;      /* margin_left */
    width: 821px;            /* box_w */
    height: 308px;           /* box_h */
  }

  .fill {
    color: #ffffff;
    font-size: 40px;
    text-shadow: 4px 4px 0px rgba(0,0,0,0.5), ...;
  }

  .title-big { font-size: 150px; }
  .title-small { font-size: 40px; }
</style>
</head>
<body>
  <div class="text">
    <div class="fill">Аліса знайшла чарівну браму золоту...</div>
  </div>
</body>
</html>
```

### 1.4 Стилізація тексту

**Text Shadow** — багатошарові тіні для глибини:
```css
text-shadow:
  4px 4px 0px rgba(0,0,0,0.5),    /* offset, blur=0 */
  4px 4px 4px rgba(0,0,0,0.5),    /* blur=4 */
  4px 4px 20px rgba(0,0,0,0.5),   /* blur=20 */
  4px 4px 40px rgba(0,0,0,0.5);   /* blur=40 */
```

**Text Stroke** — обведення через 16-точкове text-shadow:
```python
offsets = [(-w,0), (w,0), (0,-w), (0,w), (-w,-w), (-w,w), (w,-w), (w,w), ...]
# Кожна точка → "Xpx Ypx 0 rgb(R,G,B)"
```

### 1.5 Безпека (HTML санітизація)

Функція `_sanitize_title_html()` реалізує whitelist-only підхід:

| Дозволено | Приклад |
|-----------|---------|
| `<span class="title-big">` | Великий текст |
| `<span class="title-small">` | Малий текст |
| `</span>` | Закриття span |
| `<br/>` | Перенос рядка |

Все інше — `html.escape()`. Це захищає від XSS навіть якщо `{child_name}` містить HTML.

**Примітка:** В маніфесті wonderland-book використовуються класи `bold` і `large` (не `title-big`/`title-small`). Ці класи стилізуються через `bold_size` і `large_size` в style dict. Санітизатор дозволяє `<span class="...">` з будь-яким класом завдяки `allow_title_html` прапорцю, де текст передається через `_sanitize_title_html` яка ескейпить невідомі теги.

### 1.6 Шрифти з S3

```python
def _font_to_data_uri(font_uri: str) -> str:
    data = _s3_read_bytes(font_uri)  # Читає TTF/OTF з S3
    return f"data:{mime};base64,{base64.b64encode(data)}"
```

Шрифт вбудовується в HTML як base64 data URI → не потрібні зовнішні запити.

### 1.7 Блокування зовнішніх запитів

```python
async def _route(route, request):
    url = request.url
    if url.startswith("data:") or url.startswith("about:"):
        return await route.continue_()    # Дозволити data: URI
    return await route.abort()            # Заблокувати все інше
```

Playwright блокує всі HTTP запити — тільки inline data: URI дозволені.

### 1.8 Кешування шрифтів

```python
font_cache: Dict[str, str] = {}  # font_uri → data_uri
```

Шрифт читається з S3 один раз і кешується для всіх шарів одного виклику.

---

## 2. Vision Analysis (`vision_qwen.py`)

### 2.1 Концепція

Аналіз фото дитини для побудови текстового prompt, який використовується в face swap через Stable Diffusion.

```
Фото дитини
     |
     v
InsightFace (face detection + crop)
     |
     v
Qwen2-VL (vision-language model)
     |
     v
JSON: {hair_color, eyes_color, gender, hair_style, ...}
     |
     v
Prompt: "child portrait, girl, dark brown hair, curly hairstyle, high quality"
```

### 2.2 Модель

**Qwen2-VL-2B** — compact vision-language model від Alibaba:
- Розуміє зображення + текстові інструкції
- Генерує структуровані JSON відповіді
- 2B параметрів — достатньо для аналізу обличчя

### 2.3 Платформна конфігурація

| Платформа | Backend | Квантизація | Пам'ять |
|-----------|---------|-------------|---------|
| Apple Silicon (M1/M2/M3) | MPS | Без квантизації (float16) | ~4 GB |
| NVIDIA GPU | CUDA | 4-bit NF4 (bitsandbytes) | ~1.5 GB |
| Fallback | CUDA/CPU | float16 (без quantization) | ~4 GB |

```python
def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"
```

### 2.4 Singleton модель

```python
_model = None
_processor = None

def _get_model(model_id):
    global _model, _processor
    if _model is None:
        _model = Qwen2VLForConditionalGeneration.from_pretrained(...)
        _processor = AutoProcessor.from_pretrained(model_id)
    return _model, _processor
```

Модель завантажується один раз і зберігається в пам'яті для всіх наступних запитів.

### 2.5 Prompt для Qwen2-VL

**USER_PROMPT:**
```
This image has already been processed by face detection software.
I need you to analyze the facial features of the detected face only.
Provide detailed analysis in JSON format:
{
  "face_detected": true,
  "full_description": "[detailed description]",
  "hair_color": "[color]",
  "eyes_color": "[color]",
  "gender": "[boy/girl]",
  "hair_length": "[short/medium/long]",
  "hair_style": "[straight/curly/wavy/braided/etc]"
}
```

### 2.6 Алгоритм `analyze_image_pil()`

```
1. InsightFace: спробувати знайти і вирізати обличчя
   |
   +-- Знайдено → використати кроп для Qwen
   +-- Не знайдено → використати оригінал

2. Зменшити зображення до MAX_IMAGE_SIZE=1024 px (якщо більше)

3. Qwen2-VL: аналіз зображення → JSON

4. Якщо Qwen не знайшов обличчя І InsightFace не використовувався:
   - Спробувати InsightFace fallback ще раз
   - Якщо знайдено → re-analyze кропнуте обличчя

5. Нормалізувати JSON (замінити missing ключі на None)

6. Повернути dict з результатом
```

### 2.7 InsightFace Fallback

```python
def _try_insightface_fallback(pil_image):
    app = FaceAnalysis(providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
    app.prepare(ctx_id=0, det_size=(640, 640))
    faces = app.get(img_cv)

    if faces:
        face = max(faces, key=lambda x: area(x.bbox))  # Найбільше обличчя
        # Додати margin 30px, вирізати
        return Image.fromarray(cropped_rgb)
    return None
```

**Навіщо:** InsightFace краще детектує обличчя ніж Qwen2-VL. Кроп обличчя дає Qwen точніший результат аналізу.

### 2.8 JSON Guard

```python
cleaned = qwen_json_guard.extract_json(output_text)
result = json.loads(cleaned)
```

Модуль `qwen_json_guard` очищує вивід Qwen2-VL від зайвих символів (markdown code blocks, trailing text) і витягує чистий JSON.

### 2.9 Результат аналізу (приклад)

```json
{
  "face_detected": true,
  "full_description": "A young girl with dark brown curly hair, big brown eyes, olive skin tone",
  "hair_color": "dark brown",
  "eyes_color": "brown",
  "gender": "girl",
  "hair_length": "long",
  "hair_style": "curly"
}
```

Цей результат зберігається в `job.analysis_json` і використовується для побудови `job.common_prompt` в `analyze_photo_task`.

---

## 3. Бібліотеки

### html_text.py

| Бібліотека | Призначення |
|------------|-------------|
| `playwright` | Headless Chromium для HTML→PNG |
| `PIL (Pillow)` | Обробка зображень (resize, convert) |
| `boto3` | Читання шрифтів з S3 |
| `html` | Ескейпінг тексту (XSS prevention) |
| `base64` | Кодування зображень/шрифтів в data URI |
| `re` | Регулярні вирази для HTML санітизації |

### vision_qwen.py

| Бібліотека | Призначення |
|------------|-------------|
| `transformers` | Qwen2VLForConditionalGeneration, AutoProcessor |
| `torch` | GPU inference (CUDA/MPS) |
| `qwen_vl_utils` | Підготовка vision inputs для Qwen |
| `insightface` | Face detection + crop (fallback) |
| `cv2 (OpenCV)` | Image format conversion (RGB↔BGR) |
| `PIL (Pillow)` | Image resize |
