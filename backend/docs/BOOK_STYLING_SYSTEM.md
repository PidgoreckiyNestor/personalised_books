# Unified Book Styling System

> Документація принципів лейауту та стилів для генерації персоналізованих книг

---

## 1. OUTPUT / CANVAS

| Параметр | Опис | Значення |
|----------|------|----------|
| `target_size` / `page_size_px` | Розмір сторінки в пікселях | `2551` (single), `5102` (spread 2:1) |
| `dpi` | Роздільність для друку | `300` |
| `aspect_ratio` | Співвідношення сторін | `"1:1"` або `"2:1"` |

**Формула:** `2551px @ 300dpi = 8.5 inches = 21.6 cm`

---

## 2. TEXT BOX POSITIONING

| Параметр | Опис | Приклад |
|----------|------|---------|
| `box_w` | Ширина текстового блоку (px) | `1611`, `1831`, `1931` |
| `box_h` | Висота текстового блоку (px) | `1784`, `1684` |
| `top` | Відступ зверху (px) | `451`, `691`, `1451` |
| `margin_left` | Зміщення від центру (px) | `-36`, `1036`, `-1036` |
| `text_align` | Вирівнювання тексту | `"left"`, `"center"`, `"right"` |

### Layout Pattern (CSS)

```css
body {
  display: flex;
  justify-content: center;      /* центрує .text по горизонталі */
  align-items: flex-start;      /* прив'язує до верху */
}

.text {
  position: relative;
  margin-top: {top}px;          /* вертикальне позиціонування */
  margin-left: {margin_left}px; /* зміщення від центру */
  width: {box_w}px;
  height: {box_h}px;
}
```

### Positioning Examples

```
┌─────────────────────────────────┐
│           top: 300px            │
│    ┌─────────────────────┐      │
│    │   CENTERED TEXT     │      │  margin_left: 0
│    │   box_w: 1400px     │      │
│    └─────────────────────┘      │
│                                 │
└─────────────────────────────────┘

┌─────────────────────────────────┐
│                   top: 1591px   │
│              ┌──────────┐       │
│              │ RIGHT    │       │  margin_left: 1036
│              │ ALIGNED  │       │
│              └──────────┘       │
└─────────────────────────────────┘
```

---

## 3. TYPOGRAPHY

| Параметр | Опис | Default |
|----------|------|---------|
| `font_family` | Шрифт (з fallback) | `"CustomFont, 'Comic Sans MS', sans-serif"` |
| `font_size` | Розмір шрифту (px) | `70` |
| `font_weight` | Жирність | `600` |
| `line_height` | Міжрядковий інтервал | `1.15` |
| `color` | Колір тексту | `"#ffffff"` |
| `white_space` | Обробка переносів | `"pre-line"` |

### Title Classes

Для HTML-форматування заголовків (потребує `allow_title_html: true`):

```css
.title-big {
  font-size: {title_big_size}px;   /* default: font_size * 2 або font_size + 80 */
  line-height: 1.0;
  display: inline-block;
}

.title-small {
  font-size: {title_small_size}px; /* default: font_size */
  line-height: 1.05;
  display: inline-block;
}
```

### Usage Example

```json
{
  "text_template": "<span class=\"title-big\">PRINCESS</span><br/><span class=\"title-small\">and the Magic Forest</span>",
  "style": {
    "allow_title_html": true,
    "title_big_size": 180,
    "title_small_size": 100
  }
}
```

---

## 4. STROKE (OUTLINE) SYSTEM

| Параметр | Опис | Default |
|----------|------|---------|
| `stroke_width` | Товщина обводки (px) | `0` (вимкнено) |
| `stroke_color` | Колір обводки | `"#ffffff"` |

### Implementation

Обводка реалізується через **text-shadow** (надійніше за `-webkit-text-stroke`):

```python
def build_stroke_shadow_layers(stroke_width: int, stroke_color: str) -> list[str]:
    w = stroke_width
    offsets = [
        # 4 основні сторони
        (-w, 0), (w, 0), (0, -w), (0, w),
        # 4 кути
        (-w, -w), (-w, w), (w, -w), (w, w),
        # 8 проміжних точок для округлості
        (-w, -w//2), (-w, w//2), (w, -w//2), (w, w//2),
        (-w//2, -w), (w//2, -w), (-w//2, w), (w//2, w),
    ]
    return [f"{dx}px {dy}px 0 {color}" for dx, dy in offsets]
```

### Visual Result

```
stroke_width: 0          stroke_width: 3          stroke_width: 6
┌──────────┐             ┌──────────┐             ┌──────────┐
│  TEXT    │             │ ▓TEXT▓   │             │▓▓TEXT▓▓  │
└──────────┘             └──────────┘             └──────────┘
   no outline               thin outline            thick outline
```

---

## 5. SHADOW (BLUR) SYSTEM

| Параметр | Опис | Default |
|----------|------|---------|
| `shadow_color` | RGB без альфи | `"0,0,0"` (чорний) |
| `shadow_opacity` | Прозорість (0.0 - 1.0) | `1.0` |
| `shadow_offset` | Зміщення X/Y (px) | `4` |
| `shadow_blur` | Масив радіусів blur | `[0, 20, 40, 60]` |

### Multi-Layer Blur

Кілька шарів з різним blur створюють м'який, природний ефект тіні:

```python
def build_text_shadow_layers(offset, blur_list, color, opacity):
    rgba = f"rgba({color},{opacity})"
    return [f"{offset}px {offset}px {blur}px {rgba}" for blur in blur_list]
```

### Generated CSS

```css
text-shadow:
  /* Stroke layers (16 шарів) */
  -5px 0 0 rgb(255,255,255),
  5px 0 0 rgb(255,255,255),
  0 -5px 0 rgb(255,255,255),
  0 5px 0 rgb(255,255,255),
  /* ... ще 12 шарів ... */

  /* Blur layers (multi-pass) */
  4px 4px 0px rgba(0,0,0,1.0),   /* sharp shadow */
  4px 4px 20px rgba(0,0,0,1.0), /* soft glow */
  4px 4px 40px rgba(0,0,0,1.0), /* diffuse */
  4px 4px 60px rgba(0,0,0,1.0); /* ambient */
```

### Blur Presets

| Preset | `shadow_blur` | Effect |
|--------|---------------|--------|
| Sharp | `[0]` | Чітка тінь без blur |
| Soft | `[0, 10, 20]` | М'яка тінь |
| Glow | `[0, 20, 40, 60]` | Світіння навколо тексту |
| Intense | `[0, 8, 16, 24, 32, 40]` | Інтенсивне світіння |

---

## 6. SPECIAL FEATURES

### 6.1 HTML in Text

```json
{
  "text_template": "<span class=\"title-big\">{child_name}</span>",
  "style": {
    "allow_title_html": true
  }
}
```

**Allowed tags:** `<span class="title-big|title-small">`, `</span>`, `<br/>`

### 6.2 Custom Fonts

```json
{
  "font_uri": "templates/book-slug/fonts/CustomFont.ttf",
  "style": {
    "font_family": "'CustomFont', 'Comic Sans MS', sans-serif"
  }
}
```

### 6.3 Page Availability

```json
{
  "availability": {
    "prepay": true,   // показувати в preview до оплати
    "postpay": true   // включати в фінальний PDF
  }
}
```

### 6.4 Face Swap Flag

```json
{
  "needs_face_swap": true  // сторінка потребує заміни обличчя
}
```

---

## 7. MANIFEST STRUCTURE

### Full Example

```json
{
  "slug": "princess-adventure",
  "output": {
    "dpi": 300,
    "page_size_px": 2551
  },
  "covers": {
    "front": {
      "base_uri": "templates/princess-adventure/covers/front/base.png",
      "needs_face_swap": true,
      "logo_uri": "templates/princess-adventure/covers/front/logo.png",
      "crop_preview": { "x": 25, "y": 210, "w": 2551, "h": 2551 },
      "text_layers": [
        {
          "text_template": "Princess {child_name}\nand the Magic Forest",
          "style": {
            "text_align": "center",
            "font_size": 100,
            "color": "#ffffff",
            "stroke_width": 6,
            "stroke_color": "#2d5a3d"
          }
        }
      ]
    },
    "back": {
      "base_uri": "templates/princess-adventure/covers/back/base.png",
      "logo_uri": "templates/princess-adventure/covers/back/logo.png",
      "text_layers": [...]
    }
  },
  "pages": [
    {
      "page_num": 0,
      "base_uri": "templates/princess-adventure/pages/page_01.png",
      "needs_face_swap": true,
      "availability": { "prepay": true, "postpay": true },
      "text_layers": [
        {
          "text_template": "Once upon a time, {child_name} found a magical key...",
          "style": {
            "box_w": 1800,
            "top": 1850,
            "font_size": 36,
            "color": "#4a3728"
          }
        }
      ]
    }
  ]
}
```

---

## 8. STYLE INHERITANCE

```
DEFAULT_TEXT_SETTINGS (глобальні defaults)
    │
    └── page.text_layers[].style (per-layer overrides)
            │
            └── merge_settings() → фінальний CSS
```

### Default Settings (Backend)

```python
DEFAULT_TEXT_SETTINGS = {
    "target_size": 2551,
    "font_size": 70,
    "font_family": "CustomFont, 'Comic Sans MS', sans-serif",
    "font_weight": 600,
    "line_height": 1.15,
    "text_align": "left",
    "stroke_width": 0,
    "stroke_color": "#ffffff",
    "color": "#ffffff",
    "shadow_color": "0,0,0",
    "shadow_opacity": 1.0,
    "shadow_offset": 4,
    "shadow_blur": [0, 20, 40, 60],
    "box_w": 1611,
    "box_h": 1784,
    "top": 451,
    "margin_left": -36,
    "white_space": "pre-line",
}
```

---

## 9. STYLE PRESETS

### Title Page

```json
{
  "text_align": "center",
  "font_size": 110,
  "title_big_size": 180,
  "title_small_size": 100,
  "font_weight": 500,
  "color": "#ab5792",
  "stroke_width": 5,
  "stroke_color": "#ffffff",
  "shadow_opacity": 0.75,
  "shadow_blur": [0, 8, 16, 24, 32, 40]
}
```

### Body Text (Light Background)

```json
{
  "color": "#4a3728",
  "shadow_opacity": 0.0,
  "stroke_width": 2,
  "stroke_color": "#ffffff"
}
```

### Body Text (Dark Background)

```json
{
  "color": "#ffffff",
  "shadow_opacity": 0.7,
  "shadow_blur": [0, 10, 20],
  "stroke_width": 4,
  "stroke_color": "#2d5a3d"
}
```

### Centered Caption

```json
{
  "text_align": "center",
  "box_w": 1800,
  "top": 1850,
  "font_size": 36,
  "font_weight": 500
}
```

### Right-Aligned Sidebar

```json
{
  "text_align": "left",
  "box_w": 950,
  "margin_left": 1036,
  "top": 1591
}
```

---

## 10. IMPLEMENTATION FILES

| File | Purpose |
|------|---------|
| `app/book/manifest.py` | Pydantic models for manifest parsing |
| `app/rendering/html_text.py` | Production Playwright renderer |
| `scripts/preview_book_playwright.py` | Local preview with covers |
| `html_render.py` (root) | Original standalone renderer |

---

## 11. TODO / Future Improvements

- [ ] Add `font_uri` field to TextLayer model
- [ ] Support covers in BookManifest model
- [ ] Create style presets library
- [ ] Add validation for style values
- [ ] Support gradients in text color
- [ ] Add text animation presets for digital versions
