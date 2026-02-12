# Система маніфестів книг

**Файли:**
- `backend/app/book/manifest.py` — Pydantic моделі маніфесту
- `backend/app/book/manifest_store.py` — Завантаження з S3
- `backend/app/book/stages.py` — Логіка prepay/postpay стадій

---

## 1. Що таке маніфест

Маніфест (`manifest.json`) — це JSON-файл, який описує структуру книги: які сторінки є, де лежать ілюстрації, де потрібен face swap, який текст накладати, яким шрифтом, з яким стилем.

**Розташування:** `s3://bucket/templates/{slug}/manifest.json`

---

## 2. Структура template директорії

```
templates/{slug}/
  |
  +-- manifest.json              # Опис книги
  +-- pages/
  |     +-- page_00_base.png     # Базові ілюстрації
  |     +-- page_01_base.jpg
  |     +-- ...
  +-- covers/
  |     +-- front/base.jpg       # Обкладинка (перед)
  |     +-- back/base.png        # Обкладинка (зад)
  +-- fonts/
  |     +-- Rubik-Regular.ttf    # Шрифти для тексту
  |     +-- RubikBubbles-Regular.ttf
  +-- masks/                     # Маски для ComfyUI (опційно)
        +-- mask_page_01.png
```

---

## 3. Pydantic моделі (`manifest.py`)

### 3.1 Ієрархія моделей

```
BookManifest
  |
  +-- slug: str
  +-- output: OutputSpec
  |     +-- dpi: int (300)
  |     +-- page_size_px: int (2551)
  |
  +-- pages: List[PageSpec]
        |
        +-- page_num: int
        +-- base_uri: str
        +-- needs_face_swap: bool
        +-- availability: Availability
        |     +-- prepay: bool (false)
        |     +-- postpay: bool (true)
        |
        +-- text_layers: List[TextLayer]
        |     +-- text_key / text_template
        |     +-- font_uri
        |     +-- style: Dict
        |
        +-- prompt: Optional[str]
        +-- negative_prompt: Optional[str]
```

### 3.2 `BookManifest` — Головна модель

| Поле | Тип | Опис |
|------|-----|------|
| `slug` | str | Унікальний ідентифікатор книги |
| `pages` | List[PageSpec] | Список всіх сторінок |
| `output` | OutputSpec | Параметри вихідного зображення |

**Метод:** `page_by_num(page_num) → PageSpec?` — знайти сторінку по номеру.

### 3.3 `OutputSpec` — Параметри виводу

| Поле | Default | Опис |
|------|---------|------|
| `dpi` | 300 | Роздільна здатність для друку |
| `page_size_px` | 2551 | Розмір сторінки в пікселях (квадрат) |

**Приклад:** Wonderland book використовує `1080` px (для веб-preview), друковані книги — `2551` px (300 DPI на 21.6 см).

### 3.4 `PageSpec` — Специфікація сторінки

| Поле | Тип | Опис |
|------|-----|------|
| `page_num` | int | Номер сторінки (0-based) |
| `base_uri` | str | S3 шлях до базової ілюстрації |
| `needs_face_swap` | bool | Чи потрібен face swap для цієї сторінки |
| `text_layers` | List[TextLayer] | Текстові шари для накладання |
| `availability` | Availability | Доступність для prepay/postpay |
| `prompt` | str? | Prompt для Stable Diffusion (override job.common_prompt) |
| `negative_prompt` | str? | Negative prompt (override default) |

### 3.5 `Availability` — Доступність сторінки

| Поле | Default | Опис |
|------|---------|------|
| `prepay` | false | Чи доступна до оплати |
| `postpay` | true | Чи доступна після оплати |

**Типові комбінації:**
| prepay | postpay | Означає |
|--------|---------|---------|
| true | true | Доступна завжди (preview + повна версія) |
| false | true | Тільки після оплати (paywall) |
| true | false | Тільки preview (рідко) |

### 3.6 `TextLayer` — Текстовий шар

| Поле | Тип | Опис |
|------|-----|------|
| `text_key` | str? | Ключ тексту (альтернатива template) |
| `text_template` | str? | Шаблон тексту з `{child_name}`, `{child_age}` тощо |
| `template_engine` | str | Движок шаблонів: `"format"` (Python .format_map) |
| `template_vars` | List[str] | Змінні: `["child_name"]` |
| `font_uri` | str? | S3 шлях до шрифту (TTF/OTF) |
| `style` | Dict | CSS-подібні стилі рендерингу |

**Валідація:** Потрібен або `text_key`, або `text_template` (model_validator).

**Приклад text_template:**
```
<span class="bold">{child_name}</span> знайшла чарівну браму золоту.
```

**Стилі (style dict):**
| Ключ | Тип | Опис |
|------|-----|------|
| `box_w` | int | Ширина текстового блоку (px) |
| `box_h` | int | Висота текстового блоку (px) |
| `top` | int | Відступ зверху (px) |
| `margin_left` | int | Відступ зліва (px) |
| `text_align` | str | `"left"`, `"center"`, `"right"` |
| `font_size` | int | Розмір шрифту (px) |
| `font_weight` | int | 400 (normal), 700 (bold) |
| `line_height` | int | Висота рядка (px) |
| `color` | str | Колір тексту (`"#ffffff"`) |
| `bold_size` | int | Розмір для `<span class="bold">` |
| `large_size` | int | Розмір для `<span class="large">` |
| `stroke_width` | int | Товщина обведення тексту |
| `shadow_color` | str | Колір тіні (`"0,0,0"` — RGB) |
| `shadow_opacity` | float | Прозорість тіні (0-1) |
| `shadow_offset` | int | Зсув тіні (px) |
| `shadow_blur` | [int,int] | Blur тіні [x, y] |
| `allow_title_html` | bool | Дозволити HTML теги (`<span>`) |

---

## 4. Завантаження маніфесту (`manifest_store.py`)

```python
def load_manifest(slug: str) -> BookManifest:
```

**Алгоритм:**
1. Формує S3 ключ: `templates/{slug}/manifest.json`
2. Читає JSON з S3
3. Якщо `slug` відсутній в JSON — додає з параметра
4. Валідує через `BookManifest.parse_obj(data)`
5. При помилці — кидає `S3StorageError`

**Важливо:** Маніфест завантажується при кожному виклику (без кешування). Це гарантує актуальність, але додає латенсі.

---

## 5. Стадії генерації (`stages.py`)

### 5.1 Концепція prepay/postpay

```
Prepay stage:                    Postpay stage:
Генерується ПЕРЕД оплатою        Генерується ПІСЛЯ оплати
- Показує preview (1-2 стор.)    - Генерує ВСІ сторінки
- Мотивує до покупки             - Повна книга для друку
```

### 5.2 Функції

#### `prepay_page_nums(manifest) → List[int]`

Повертає **першу і останню** видимі сторінки книги (для preview).

```python
def prepay_page_nums(manifest):
    candidates = front_visible_page_nums(manifest)  # Всі видимі
    if len(candidates) <= 1:
        return candidates
    return [candidates[0], candidates[-1]]  # Перша + остання
```

**Приклад:** Для книги з 30 сторінками (0-29), після виключення прихованих (1, 23), повертає `[0, 29]`.

#### `page_nums_for_stage(manifest, stage) → List[int]`

| Stage | Повертає |
|-------|---------|
| `prepay` | Перша + остання видима сторінка |
| `postpay` | Всі сторінки де `availability.postpay = true` |

#### `front_visible_page_nums(manifest) → List[int]`

Всі сторінки маніфесту **мінус приховані** (FRONT_HIDDEN_PAGE_NUMS = {1, 23}).

**Чому приховані?** Сторінки 1 і 23 — це спеціальні face swap сторінки без тексту, які не показуються у фронтенд preview, але генеруються для повної книги.

#### `stage_has_face_swap(manifest, stage) → bool`

Перевіряє чи хоча б одна сторінка в stage має `needs_face_swap = true`. Використовується щоб пропустити GPU таск для text-only stages.

---

## 6. Приклад маніфесту (скорочений)

```json
{
  "slug": "wonderland-book",
  "title": "{child_name} у Дивокраї",
  "language": "uk",
  "output": {
    "dpi": 300,
    "page_size_px": 1080
  },
  "covers": {
    "front": {
      "base_uri": "templates/wonderland-book/covers/front/base.jpg",
      "needs_face_swap": true,
      "text_layers": [
        {
          "text_template": "{child_name} у Дивокраї",
          "font_uri": "templates/wonderland-book/fonts/RubikBubbles-Regular.ttf",
          "style": {
            "font_size": 100,
            "color": "#ffffff",
            "text_align": "center",
            "box_w": 547,
            "box_h": 268,
            "top": 80,
            "margin_left": 266
          }
        }
      ]
    }
  },
  "pages": [
    {
      "page_num": 0,
      "base_uri": "templates/wonderland-book/pages/page_00_base.png",
      "needs_face_swap": false,
      "availability": {"prepay": true, "postpay": true},
      "text_layers": [
        {
          "text_template": "<span class=\"bold\">{child_name}</span> знайшла чарівну браму...",
          "font_uri": "templates/wonderland-book/fonts/Rubik-Regular.ttf",
          "style": {
            "text_align": "left",
            "font_size": 40,
            "line_height": 55,
            "color": "#ffffff",
            "shadow_opacity": 0.5,
            "box_w": 821, "box_h": 308,
            "top": 630, "margin_left": 129,
            "bold_size": 56,
            "allow_title_html": true
          }
        }
      ]
    },
    {
      "page_num": 1,
      "base_uri": "templates/wonderland-book/pages/page_01_base.jpg",
      "needs_face_swap": true,
      "availability": {"prepay": true, "postpay": true},
      "description": "Дівчинка біля золотої брами"
    }
  ]
}
```

---

## 7. Типи сторінок у маніфесті

| Тип | needs_face_swap | text_layers | Приклад |
|-----|----------------|-------------|---------|
| **Пейзаж з текстом** | false | є | Цукеркова стежка з описом |
| **Face swap без тексту** | true | немає | Дівчинка біля брами |
| **Face swap з текстом** | true | є | Дівчинка з какао + підпис |
| **Пейзаж без тексту** | false | немає | Фонова ілюстрація |

**Статистика wonderland-book (30 сторінок):**
- Face swap: 10 сторінок (1, 2, 5, 7, 9, 13, 17, 19, 23, 24, 26, 29)
- Тільки текст: 18 сторінок
- Без тексту: кілька face swap сторінок

---

## 8. Потік обробки маніфесту

```
1. POST /generate/ → load_manifest(slug)
2. Визначити stage (prepay/postpay)
3. page_nums_for_stage() → [0, 29]  (для prepay)
4. stage_has_face_swap() → true?
   YES → build_stage_backgrounds_task (GPU)
         Для кожної сторінки:
           needs_face_swap? → ComfyUI face transfer
           !needs_face_swap → copy base image
   NO  → render_stage_pages_task (CPU) напряму
5. render_stage_pages_task (CPU)
   Для кожної сторінки:
     Завантажити bg з S3
     text_layers є? → render_text_layers_over_image()
                       Підставити {child_name} → "Аліса"
     text_layers немає? → використати bg як фінал
     Зберегти layout/{job_id}/pages/page_XX.png
```
