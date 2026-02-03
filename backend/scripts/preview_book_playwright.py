#!/usr/bin/env python3
"""
Preview book pages AND covers using Playwright HTML rendering.
Supports the full manifest structure including covers.

Usage:
    python3 -m scripts.preview_book_playwright --slug test-princess --child-name "Софія"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import html
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from playwright.async_api import async_playwright


# ============ Rendering logic (adapted from html_text.py) ============

DEFAULT_TEXT_SETTINGS: Dict[str, Any] = {
    "target_size": 1080,
    "font_size": 70,
    "font_family": "'Arial Unicode MS', 'Comic Sans MS', sans-serif",
    "font_weight": 600,
    "text_align": "left",
    "stroke_width": 0,
    "stroke_color": "#ffffff",
    "color": "#ffffff",
    "shadow_color": "0,0,0",
    "shadow_opacity": 1.0,
    "shadow_offset": 4,
    "box_w": 1611,
    "box_h": 1784,
    "top": 451,
    "margin_left": 0,
    "white_space": "pre-line",
}


def _merge_settings(defaults: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(defaults)
    if override:
        s.update(override)
    return s


def _hex_to_rgb(hex_color: str) -> tuple:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return (0, 0, 0)
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


# HTML sanitizer patterns (extended from production)
_ALLOWED_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ALLOWED_SPAN_OPEN_RE = re.compile(
    r"""^<\s*span\s+class\s*=\s*(?P<q>['"])\s*(?P<cls>title-big|title-small|bold|large|bold\s+large|large\s+bold)\s*(?P=q)\s*>\s*$""",
    flags=re.IGNORECASE,
)
_ALLOWED_SPAN_CLOSE_RE = re.compile(r"^<\s*/\s*span\s*>\s*$", flags=re.IGNORECASE)
_ALLOWED_BR_RE = re.compile(r"^<\s*br\s*/?\s*>\s*$", flags=re.IGNORECASE)


def _sanitize_html(text_html: str) -> str:
    """Whitelist-only HTML sanitizer for preview."""
    out: List[str] = []
    last_end = 0
    for m in _ALLOWED_HTML_TAG_RE.finditer(text_html):
        start, end = m.span()
        if start > last_end:
            out.append(html.escape(text_html[last_end:start]))
        tag = text_html[start:end]
        if (_ALLOWED_SPAN_OPEN_RE.match(tag) or
            _ALLOWED_SPAN_CLOSE_RE.match(tag) or
            _ALLOWED_BR_RE.match(tag)):
            out.append(tag)
        else:
            out.append(html.escape(tag))
        last_end = end
    if last_end < len(text_html):
        out.append(html.escape(text_html[last_end:]))
    return "".join(out)


def _build_text_shadow_layers(
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
) -> List[str]:
    color_with_alpha = f"rgba({shadow_color},{shadow_opacity})"
    return [f"{shadow_offset}px {shadow_offset}px {blur}px {color_with_alpha}" for blur in shadow_blur]


def _build_stroke_shadow_layers(stroke_width: int, stroke_color: str) -> List[str]:
    if stroke_width <= 0:
        return []
    r, g, b = _hex_to_rgb(stroke_color)
    c = f"rgb({r},{g},{b})"
    w = stroke_width
    offsets = [
        (-w, 0), (w, 0), (0, -w), (0, w),
        (-w, -w), (-w, w), (w, -w), (w, w),
        (-w, -w // 2), (-w, w // 2), (w, -w // 2), (w, w // 2),
        (-w // 2, -w), (w // 2, -w), (-w // 2, w), (w // 2, w),
    ]
    return [f"{dx}px {dy}px 0 {c}" for dx, dy in offsets if dx or dy]


def _build_text_shadow_css(
    stroke_width: int,
    stroke_color: str,
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
) -> str:
    layers: List[str] = []
    layers.extend(_build_stroke_shadow_layers(stroke_width, stroke_color))
    layers.extend(_build_text_shadow_layers(shadow_offset, shadow_blur, shadow_color, shadow_opacity))
    return ",\n  ".join(layers) if layers else "none"


def _pil_to_png_data_uri(img: Image.Image, target_size: int) -> str:
    if img.size != (target_size, target_size):
        img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _font_to_data_uri(font_path: Path) -> str:
    if not font_path.exists():
        return ""
    data = font_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    mime = "font/ttf" if font_path.suffix.lower() == ".ttf" else "font/otf"
    return f"data:{mime};base64,{b64}"


def _build_html(
    bg_data_uri: str,
    font_data_uri: str,
    text: str,
    settings_dict: Dict[str, Any],
    bold_font_data_uri: str = "",
) -> str:
    target_size = settings_dict["target_size"]
    stroke_width = int(settings_dict.get("stroke_width", 0) or 0)
    stroke_color = str(settings_dict.get("stroke_color", "#ffffff") or "#ffffff")

    title_big_size = int(
        settings_dict.get("title_big_size", max(int(settings_dict["font_size"]) * 2, int(settings_dict["font_size"]) + 80))
    )
    title_small_size = int(settings_dict.get("title_small_size", int(settings_dict["font_size"])))

    # Bold and large sizes for custom spans
    bold_size = int(settings_dict.get("bold_size", int(settings_dict["font_size"]) + 16))
    large_size = int(settings_dict.get("large_size", int(settings_dict["font_size"]) + 38))

    text_shadow_css = _build_text_shadow_css(
        stroke_width=stroke_width,
        stroke_color=stroke_color,
        shadow_offset=int(settings_dict.get("shadow_offset", 4)),
        shadow_blur=list(settings_dict.get("shadow_blur", [0, 20, 40])),
        shadow_color=str(settings_dict.get("shadow_color", "0,0,0")),
        shadow_opacity=float(settings_dict.get("shadow_opacity", 0.7)),
    )

    # Check if HTML is allowed (for bold/italic spans)
    allow_html = settings_dict.get("allow_title_html", False)
    if allow_html:
        # Whitelist-only HTML sanitizer
        safe_text = _sanitize_html(text)
    else:
        # Escape all HTML to prevent injection
        safe_text = html.escape(text)

    font_face = ""
    if font_data_uri:
        font_face = f"""
@font-face {{
  font-family: 'CustomFont';
  src: url('{font_data_uri}');
  font-weight: 400;
}}
"""
    if bold_font_data_uri:
        font_face += f"""
@font-face {{
  font-family: 'CustomFont';
  src: url('{bold_font_data_uri}');
  font-weight: 700;
}}
"""

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
{font_face}

html, body {{
  margin: 0;
  padding: 0;
  width: {target_size}px;
  height: {target_size}px;
  overflow: hidden;
}}

body {{
  background: url('{bg_data_uri}') center center / cover no-repeat;
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
}}

.text {{
  position: relative;
  margin-top: {settings_dict['top']}px;
  margin-left: {settings_dict['margin_left']}px;
  width: {settings_dict['box_w']}px;
  height: {settings_dict['box_h']}px;
}}

.fill {{
  color: {settings_dict['color']};
  font-family: {settings_dict['font_family']};
  font-size: {settings_dict['font_size']}px;
  font-weight: {settings_dict['font_weight']};
  text-align: {settings_dict['text_align']};
  white-space: {settings_dict.get('white_space', 'pre-line')};

  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;

  /* Stroke (outline) */
  text-stroke: {stroke_width}px {stroke_color};
  -webkit-text-stroke: {stroke_width}px {stroke_color};
  paint-order: stroke fill;

  text-shadow:
{text_shadow_css};
}}

.fill * {{
  /* Ensure stroke applies to nested spans (text-stroke is not inherited) */
  -webkit-text-stroke: inherit;
  text-stroke: inherit;
  paint-order: inherit;
}}

.title-big {{
  font-size: {title_big_size}px;
  line-height: 1.0;
  display: inline-block;
}}

.title-small {{
  font-size: {title_small_size}px;
  line-height: 1.05;
  display: inline-block;
}}

/* Bold text class - used in manifest with <span class="bold"> */
.bold {{
  font-size: {bold_size}px;
  font-weight: 700;
  line-height: 1.0;
  display: inline;
  vertical-align: baseline;
}}

/* Large text class - used in manifest with <span class="large"> */
.large {{
  font-size: {large_size}px;
}}

/* Combined bold large */
.bold.large {{
  font-size: {large_size}px;
  font-weight: 700;
}}
</style>
</head>

<body>
  <div class="text">
    <div class="fill">{safe_text}</div>
  </div>
</body>
</html>
"""


async def render_text_layers(
    bg_img: Image.Image,
    text_layers: List[Dict],
    template_vars: Dict[str, Any],
    output_px: int,
    fonts_dir: Path = None,
) -> Image.Image:
    """Render text layers over background image using Playwright."""
    if not text_layers:
        return bg_img

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        cur = bg_img
        for layer in text_layers:
            # Get text template and render with variables
            template = layer.get("text_template", "") or layer.get("text_key", "")
            try:
                text = template.format_map(template_vars)
            except Exception as e:
                print(f"    Warning: template error: {e}")
                text = template

            # Merge settings
            style = _merge_settings(DEFAULT_TEXT_SETTINGS, layer.get("style", {}))
            style["target_size"] = output_px

            # Convert background to data URI
            bg_data_uri = _pil_to_png_data_uri(cur, output_px)

            # Load font if specified
            font_data_uri = ""
            bold_font_data_uri = ""
            font_uri = layer.get("font_uri", "")
            if font_uri and fonts_dir:
                font_name = Path(font_uri).name
                font_path = fonts_dir / font_name
                if font_path.exists():
                    font_data_uri = _font_to_data_uri(font_path)
                    style["font_family"] = f"'CustomFont', {style['font_family']}"

                    # Try to load bold variant (e.g., Rubik-Regular.ttf -> Rubik-Bold.ttf)
                    bold_font_name = font_name.replace("-Regular", "-Bold").replace("_Regular", "_Bold")
                    bold_font_path = fonts_dir / bold_font_name
                    if bold_font_path.exists() and bold_font_path != font_path:
                        bold_font_data_uri = _font_to_data_uri(bold_font_path)

            # Build HTML
            html_doc = _build_html(bg_data_uri, font_data_uri, text, style, bold_font_data_uri)

            # Render with Playwright
            page = await browser.new_page(viewport={"width": output_px, "height": output_px})
            try:
                await page.set_content(html_doc, wait_until="load")
                await asyncio.sleep(0.3)  # Let fonts load
                png_bytes = await page.screenshot(type="png")
            finally:
                await page.close()

            cur = Image.open(io.BytesIO(png_bytes)).convert("RGB")

        await browser.close()

    return cur


# ============ Book preview logic ============

def load_manifest(manifest_path: Path) -> dict:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("slug", manifest_path.parent.name)
    return data


def load_local_image(base_uri: str, templates_dir: Path, slug: str) -> Image.Image:
    if base_uri.startswith("templates/"):
        rel_path = base_uri[len("templates/"):]
    else:
        rel_path = base_uri

    # First try exact path match
    candidates = [
        templates_dir / rel_path,                          # Exact: templates/test-princess/covers/back/base.png
        templates_dir / slug / rel_path,                   # With slug prefix
        templates_dir.parent / base_uri,                   # Parent directory
        templates_dir / slug / "pages" / Path(rel_path).name,  # Fallback to pages
    ]

    for path in candidates:
        if path.exists():
            return Image.open(path).convert("RGB")

    print(f"  Warning: Image not found: {base_uri}")
    return Image.new("RGB", (2551, 2551), (200, 200, 200))


def load_logo_image(logo_uri: str, templates_dir: Path, slug: str) -> Optional[Image.Image]:
    """Load logo image preserving transparency (RGBA)."""
    if not logo_uri:
        return None

    if logo_uri.startswith("templates/"):
        rel_path = logo_uri[len("templates/"):]
    else:
        rel_path = logo_uri

    # First try exact path match
    candidates = [
        templates_dir / rel_path,              # Exact: test-princess/covers/back/logo.png
        templates_dir / slug / rel_path,       # With slug prefix
    ]

    for path in candidates:
        if path.exists():
            img = Image.open(path)
            # Keep RGBA for transparency
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            return img

    return None


def overlay_logo(img: Image.Image, logo_uri: str, templates_dir: Path, slug: str) -> Image.Image:
    """Overlay logo on image if logo exists."""
    if not logo_uri:
        return img

    logo_img = load_logo_image(logo_uri, templates_dir, slug)
    if logo_img is None:
        print(f"    Warning: Logo not found: {logo_uri}")
        return img

    # Convert base image to RGBA
    img_rgba = img.convert("RGBA")

    # Position logo (centered at bottom by default, or scale to fit)
    # Logo is typically smaller, place it at bottom center
    logo_w, logo_h = logo_img.size
    img_w, img_h = img_rgba.size

    # If logo is much smaller than image, position it (bottom center with padding)
    if logo_w < img_w * 0.8 and logo_h < img_h * 0.8:
        x = (img_w - logo_w) // 2
        y = img_h - logo_h - 50  # 50px from bottom
    else:
        # Logo is large, resize to fit
        logo_img = logo_img.resize((img_w, img_h), Image.Resampling.LANCZOS)
        x, y = 0, 0

    # Composite with transparency
    img_rgba.paste(logo_img, (x, y), logo_img)
    return img_rgba.convert("RGB")


async def preview_cover(
    manifest: dict,
    cover_spec: dict,
    cover_type: str,  # "front" or "back"
    templates_dir: Path,
    template_vars: Dict[str, Any],
    output_dir: Path = None,
) -> Optional[Image.Image]:
    """Render a cover (front or back)."""
    base_uri = cover_spec.get("base_uri")
    if not base_uri:
        return None

    text_layers = cover_spec.get("text_layers", [])
    logo_uri = cover_spec.get("logo_uri")

    print(f"  Cover ({cover_type}): {Path(base_uri).name}")

    slug = manifest["slug"]
    output_px = manifest.get("output", {}).get("page_size_px", 1080)

    # Load base image
    img = load_local_image(base_uri, templates_dir, slug)

    # Resize if needed
    if img.size != (output_px, output_px):
        img = img.resize((output_px, output_px), Image.Resampling.LANCZOS)

    # Render text layers
    if text_layers:
        fonts_dir = templates_dir / slug / "fonts"
        img = await render_text_layers(
            img,
            text_layers,
            template_vars,
            output_px,
            fonts_dir if fonts_dir.exists() else None,
        )
        print(f"    + Rendered {len(text_layers)} text layer(s)")

    # Overlay logo
    if logo_uri:
        img = overlay_logo(img, logo_uri, templates_dir, slug)
        print(f"    + Logo overlay")

    # Save if output_dir provided
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"cover_{cover_type}.png"
        img.save(out_path, "PNG")
        print(f"    Saved: {out_path.name}")

    return img


async def preview_page(
    manifest: dict,
    page_spec: dict,
    templates_dir: Path,
    template_vars: Dict[str, Any],
    output_dir: Path = None,
) -> Image.Image:
    page_num = page_spec["page_num"]
    base_uri = page_spec["base_uri"]
    text_layers = page_spec.get("text_layers", [])

    print(f"  Page {page_num}: {Path(base_uri).name}")

    slug = manifest["slug"]
    output_px = manifest.get("output", {}).get("page_size_px", 1080)

    # Load base image
    img = load_local_image(base_uri, templates_dir, slug)

    # Resize if needed
    if img.size != (output_px, output_px):
        img = img.resize((output_px, output_px), Image.Resampling.LANCZOS)

    # Render text layers
    if text_layers:
        fonts_dir = templates_dir / slug / "fonts"
        img = await render_text_layers(
            img,
            text_layers,
            template_vars,
            output_px,
            fonts_dir if fonts_dir.exists() else None,
        )
        print(f"    + Rendered {len(text_layers)} text layer(s)")

    # Save if output_dir provided
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"page_{page_num:02d}.png"
        img.save(out_path, "PNG")
        print(f"    Saved: {out_path.name}")

    return img


def create_pdf(images: List[Image.Image], output_path: Path, cover_front: Image.Image = None, cover_back: Image.Image = None):
    """Create PDF with optional covers."""
    all_images = []

    # Add front cover first
    if cover_front:
        all_images.append(cover_front)
        print(f"  + Front cover added to PDF")

    # Add pages
    all_images.extend(images)

    # Add back cover last
    if cover_back:
        all_images.append(cover_back)
        print(f"  + Back cover added to PDF")

    if not all_images:
        print("No images to create PDF")
        return

    rgb_images = [img.convert("RGB") for img in all_images]
    rgb_images[0].save(
        output_path,
        "PDF",
        save_all=True,
        append_images=rgb_images[1:] if len(rgb_images) > 1 else [],
        resolution=300,
    )
    print(f"\nPDF saved: {output_path} ({len(rgb_images)} pages)")


async def main_async(args):
    # Find templates directory
    if args.templates_dir:
        templates_dir = args.templates_dir
    else:
        candidates = [
            Path(__file__).parent.parent / "templates",
            Path("templates"),
            Path("backend/templates"),
        ]
        templates_dir = next((c for c in candidates if c.exists()), None)

        if not templates_dir:
            print("Error: Templates directory not found")
            sys.exit(1)

    manifest_path = templates_dir / args.slug / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        sys.exit(1)

    print(f"\nPreview: {args.slug}")
    print(f"Child: {args.child_name}, Age: {args.child_age}")
    print(f"Templates: {templates_dir}\n")

    manifest = load_manifest(manifest_path)

    # Count elements
    num_pages = len(manifest.get("pages", []))
    has_covers = "covers" in manifest
    print(f"Book: {manifest['slug']}")
    print(f"  Pages: {num_pages}")
    print(f"  Covers: {'Yes (front + back)' if has_covers else 'No'}")
    print()

    template_vars = {
        "child_name": args.child_name,
        "child_age": args.child_age,
        "child_gender": "girl",
    }

    # Render covers
    cover_front = None
    cover_back = None

    if has_covers and not args.no_covers:
        covers = manifest.get("covers", {})

        if covers.get("front"):
            cover_front = await preview_cover(
                manifest, covers["front"], "front",
                templates_dir, template_vars, args.output
            )

        if covers.get("back"):
            cover_back = await preview_cover(
                manifest, covers["back"], "back",
                templates_dir, template_vars, args.output
            )

    # Render pages
    images = []
    pages = sorted(manifest.get("pages", []), key=lambda p: p["page_num"])

    if args.page is not None:
        pages = [p for p in pages if p["page_num"] == args.page]

    for page_spec in pages:
        img = await preview_page(manifest, page_spec, templates_dir, template_vars, args.output)
        images.append(img)

    # Create PDF
    if args.pdf and (images or cover_front or cover_back):
        print("\nGenerating PDF...")
        create_pdf(images, args.pdf, cover_front, cover_back)

    total = len(images) + (1 if cover_front else 0) + (1 if cover_back else 0)
    print(f"\nDone! {total} element(s) rendered")

    if not args.output and not args.pdf:
        print("Tip: Use --output ./preview or --pdf book.pdf to save")


def main():
    parser = argparse.ArgumentParser(description="Preview book pages and covers with Playwright")
    parser.add_argument("--slug", required=True, help="Book slug")
    parser.add_argument("--child-name", required=True, help="Child name")
    parser.add_argument("--child-age", type=int, default=5, help="Child age")
    parser.add_argument("--page", type=int, help="Specific page number")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--pdf", type=Path, help="Output PDF path")
    parser.add_argument("--templates-dir", type=Path, help="Templates directory")
    parser.add_argument("--no-covers", action="store_true", help="Skip covers rendering")

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
