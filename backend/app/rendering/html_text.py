from __future__ import annotations

import asyncio
import base64
import html
import io
import mimetypes
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

import boto3
from PIL import Image
from playwright.async_api import async_playwright

from ..config import settings
from ..logger import logger
from ..book.manifest import TextLayer, TypographySpec
from .layout import resolve_text_box


_s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION_NAME,
    endpoint_url=settings.AWS_ENDPOINT_URL,
)


# ---------------------------------------------------------------------------
# pt ↔ px helpers
# ---------------------------------------------------------------------------

def _pt_val(val: Any, pt_to_px: float) -> int:
    """Parse "14pt" → px, or pass through numeric value."""
    if isinstance(val, str) and val.endswith("pt"):
        return round(float(val[:-2]) * pt_to_px)
    return int(val)


def _build_defaults_from_typography(
    typo: TypographySpec,
    output_px: int,
    dpi: int,
) -> Dict[str, Any]:
    """Build a settings dict from book-level typography."""
    pt_to_px = dpi / 72
    body = typo.body
    return {
        "target_size": output_px,
        "font_size": _pt_val(body.get("font_size", "14pt"), pt_to_px),
        "line_height": _pt_val(body.get("line_height", "19pt"), pt_to_px),
        "color": body.get("color", "#ffffff"),
        "font_family": "CustomFont, 'Comic Sans MS', sans-serif",
        "font_weight": int(body.get("font_weight", 400)),
        "white_space": "pre-line",
        # shadow
        "shadow_color": typo.shadow.color,
        "shadow_opacity": typo.shadow.opacity,
        "shadow_offset": typo.shadow.offset,
        "shadow_offset_x": typo.shadow.offset_x,
        "shadow_offset_y": typo.shadow.offset_y,
        "shadow_blur": list(typo.shadow.blur),
        "stroke_width": int(body.get("stroke_width", 0)),
        "stroke_color": str(body.get("stroke_color", "#ffffff")),
        "letter_spacing": float(body.get("letter_spacing", 0.5)),
    }


def _accent_size(typo: TypographySpec, pt_to_px: float) -> int:
    return _pt_val(typo.accent.get("font_size", "19pt"), pt_to_px)


def _accent_color(typo: TypographySpec) -> str | None:
    return typo.accent.get("color")


# ---------------------------------------------------------------------------
# Markdown → HTML
# ---------------------------------------------------------------------------

_BOLD_RE = re.compile(r'\*\*(.+?)\*\*', flags=re.DOTALL)


def _markdown_to_html(text: str) -> str:
    """Convert **text** → <span class="accent">text</span>, escape everything else."""
    parts = _BOLD_RE.split(text)
    out: List[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            out.append(html.escape(part))
        else:
            out.append(f'<span class="accent">{html.escape(part)}</span>')
    return "".join(out)


# ---------------------------------------------------------------------------
# Shadow / stroke CSS builders (unchanged logic)
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_color!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _build_text_shadow_layers(
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
    shadow_offset_x: int | None = None,
    shadow_offset_y: int | None = None,
) -> List[str]:
    color_with_alpha = f"rgba({shadow_color},{shadow_opacity})"
    ox = shadow_offset_x if shadow_offset_x is not None else shadow_offset
    oy = shadow_offset_y if shadow_offset_y is not None else shadow_offset
    return [f"{ox}px {oy}px {blur}px {color_with_alpha}" for blur in shadow_blur]


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
    *,
    stroke_width: int,
    stroke_color: str,
    shadow_offset: int,
    shadow_blur: List[int],
    shadow_color: str,
    shadow_opacity: float,
    shadow_offset_x: int | None = None,
    shadow_offset_y: int | None = None,
) -> str:
    layers: List[str] = []
    layers.extend(_build_stroke_shadow_layers(stroke_width, stroke_color))
    layers.extend(_build_text_shadow_layers(
        shadow_offset, shadow_blur, shadow_color, shadow_opacity,
        shadow_offset_x=shadow_offset_x, shadow_offset_y=shadow_offset_y,
    ))
    return ",\n  ".join(layers) if layers else "none"


# ---------------------------------------------------------------------------
# S3 / data-URI helpers
# ---------------------------------------------------------------------------

def _bytes_to_data_uri(data: bytes, mime: str) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pil_to_png_data_uri(img: Image.Image, target_size: int) -> str:
    if img.size != (target_size, target_size):
        img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return _bytes_to_data_uri(buf.getvalue(), "image/png")


def _s3_read_bytes(uri_or_key: str) -> bytes:
    bucket = settings.S3_BUCKET_NAME
    key = uri_or_key
    if uri_or_key.startswith("s3://"):
        p = urlparse(uri_or_key)
        bucket = p.netloc or bucket
        key = p.path.lstrip("/")
    obj = _s3.get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def _font_to_data_uri(font_uri: str) -> str:
    mime, _ = mimetypes.guess_type(font_uri)
    if not mime:
        mime = "application/octet-stream"
    data = _s3_read_bytes(font_uri)
    return _bytes_to_data_uri(data, mime)


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _render_template(layer: TextLayer, template_vars: Dict[str, Any]) -> str:
    if layer.text_template:
        template = layer.text_template
    elif layer.text_key:
        template = layer.text_key
    else:
        raise ValueError("TextLayer has neither text_template nor text_key")

    engine = (layer.template_engine or "format").lower().strip()
    if engine != "format":
        raise ValueError(f"Unsupported template_engine: {layer.template_engine}")

    try:
        rendered = template.format_map(template_vars)
    except Exception as e:
        raise ValueError(f"Template render failed: {e}")

    return rendered


# ---------------------------------------------------------------------------
# HTML document builder
# ---------------------------------------------------------------------------

def _build_html(
    bg_data_uri: str,
    font_data_uri: str,
    text_html: str,
    settings_dict: Dict[str, Any],
    *,
    accent_size: int,
    accent_color: str | None = None,
    font_bold_data_uri: str = "",
) -> str:
    target_size = settings_dict["target_size"]
    stroke_width = int(settings_dict.get("stroke_width", 0) or 0)
    stroke_color = str(settings_dict.get("stroke_color", "#ffffff") or "#ffffff")

    # Compensate half-leading so text visually aligns with safe zone edge
    half_leading = (settings_dict["line_height"] - settings_dict["font_size"]) / 2
    v_align = settings_dict.get("v_align", "flex-start")
    if v_align == "flex-end":
        leading_compensation = f"margin-bottom: -{half_leading}px;"
    elif v_align == "flex-start":
        leading_compensation = f"margin-top: -{half_leading}px;"
    else:
        leading_compensation = ""

    text_shadow_css = _build_text_shadow_css(
        stroke_width=stroke_width,
        stroke_color=stroke_color,
        shadow_offset=int(settings_dict["shadow_offset"]),
        shadow_blur=list(settings_dict["shadow_blur"]),
        shadow_color=str(settings_dict["shadow_color"]),
        shadow_opacity=float(settings_dict["shadow_opacity"]),
        shadow_offset_x=settings_dict.get("shadow_offset_x"),
        shadow_offset_y=settings_dict.get("shadow_offset_y"),
    )

    font_face = ""
    if font_data_uri:
        font_face = f"""
@font-face {{
  font-family: 'CustomFont';
  font-weight: 400;
  src: url('{font_data_uri}');
}}
""".strip()
        if font_bold_data_uri:
            font_face += f"""

@font-face {{
  font-family: 'CustomFont';
  font-weight: 700;
  src: url('{font_bold_data_uri}');
}}
"""

    accent_color_css = accent_color or settings_dict["color"]

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
  display: flex;
  flex-direction: column;
  justify-content: {settings_dict['v_align']};
}}

.fill {{
  color: {settings_dict['color']};
  font-family: {settings_dict['font_family']};
  font-size: {settings_dict['font_size']}px;
  font-weight: {settings_dict['font_weight']};
  line-height: {settings_dict['line_height']}px;
  text-align: {settings_dict['text_align']};
  white-space: {settings_dict['white_space']};
  letter-spacing: {settings_dict['letter_spacing']}px;
  {leading_compensation}

  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;

  text-stroke: {stroke_width}px {stroke_color};
  -webkit-text-stroke: {stroke_width}px {stroke_color};
  paint-order: stroke fill;

  text-shadow:
  {text_shadow_css};
}}

.fill * {{
  -webkit-text-stroke: inherit;
  text-stroke: inherit;
  paint-order: inherit;
}}

.accent {{
  font-size: {accent_size}px;
  font-weight: 700;
  color: {accent_color_css};
  display: inline;
}}
</style>
</head>

<body>
  <div class="text">
    <div class="fill">{text_html}</div>
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main rendering function
# ---------------------------------------------------------------------------

async def render_text_layers_over_image(
    bg_img: Image.Image,
    layers: List[TextLayer],
    *,
    template_vars: Dict[str, Any],
    output_px: int,
    typography: TypographySpec,
    dpi: int,
    safe_zone_pt: float,
) -> Image.Image:
    """
    Render text layers over a background image using Playwright.

    Uses book-level typography for defaults. Each layer specifies grid position
    and optional style overrides. Text uses markdown (**accent**) for emphasis.
    """
    if not layers:
        return bg_img

    pt_to_px = dpi / 72
    defaults = _build_defaults_from_typography(typography, output_px, dpi)
    accent_sz = _accent_size(typography, pt_to_px)
    accent_clr = _accent_color(typography)

    font_cache: Dict[str, str] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        cur = bg_img
        for layer in layers:
            rendered_text = _render_template(layer, template_vars)
            text_html = _markdown_to_html(rendered_text)

            # Resolve grid position → pixel coordinates
            box = resolve_text_box(
                position=layer.position,
                box_width=layer.box_width,
                offset_x_pt=layer.offset_x,
                offset_y_pt=layer.offset_y,
                page_size_px=output_px,
                safe_zone_pt=safe_zone_pt,
                dpi=dpi,
            )

            # Build style: typography defaults → layer overrides → position
            style = dict(defaults)
            if layer.style:
                for k, v in layer.style.items():
                    if k in ("shadow_color", "shadow_opacity", "shadow_offset", "shadow_blur",
                             "shadow_offset_x", "shadow_offset_y",
                             "stroke_width", "stroke_color", "color", "font_weight",
                             "font_size", "line_height", "letter_spacing"):
                        # Convert pt strings to px for font_size/line_height
                        if k in ("font_size", "line_height") and isinstance(v, str):
                            style[k] = _pt_val(v, pt_to_px)
                        else:
                            style[k] = v
            style["target_size"] = output_px
            style["top"] = box["top"]
            style["margin_left"] = box["margin_left"]
            style["box_w"] = box["box_w"]
            style["box_h"] = box["box_h"]
            style["text_align"] = layer.text_align
            style["v_align"] = box.get("v_align", "flex-start")

            bg_data_uri = _pil_to_png_data_uri(cur, output_px)

            # Font: layer override → typography
            font_uri = layer.font_uri or typography.font_uri
            font_data_uri = ""
            font_bold_data_uri = ""

            if font_uri:
                if font_uri not in font_cache:
                    font_cache[font_uri] = _font_to_data_uri(font_uri)
                font_data_uri = font_cache[font_uri]

                # Bold font: explicit → auto-derive from Regular→Bold
                font_bold_uri = typography.font_bold_uri
                if not font_bold_uri and "Regular" in os.path.basename(font_uri):
                    font_bold_uri = font_uri.replace("Regular", "Bold")

                if font_bold_uri:
                    if font_bold_uri not in font_cache:
                        try:
                            font_cache[font_bold_uri] = _font_to_data_uri(font_bold_uri)
                        except Exception:
                            logger.debug(f"Bold font variant not found: {font_bold_uri}")
                            font_cache[font_bold_uri] = ""
                    font_bold_data_uri = font_cache[font_bold_uri]

            html_doc = _build_html(
                bg_data_uri=bg_data_uri,
                font_data_uri=font_data_uri,
                text_html=text_html,
                settings_dict=style,
                accent_size=accent_sz,
                accent_color=accent_clr,
                font_bold_data_uri=font_bold_data_uri,
            )

            page = await browser.new_page(viewport={"width": output_px, "height": output_px})
            try:
                async def _route(route, request):
                    url = request.url or ""
                    if url.startswith("data:") or url.startswith("about:"):
                        return await route.continue_()
                    return await route.abort()

                await page.route("**/*", _route)
                await page.set_content(html_doc, wait_until="load")
                await asyncio.sleep(0.3)
                png_bytes = await page.screenshot(type="png")
            finally:
                await page.close()

            cur = Image.open(io.BytesIO(png_bytes)).convert("RGB")

        await browser.close()

    logger.debug("Rendered text layers via Playwright", extra={"layers": len(layers), "output_px": output_px})
    return cur
