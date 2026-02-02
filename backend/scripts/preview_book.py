#!/usr/bin/env python3
"""
Preview book pages locally without S3.

Usage:
    python -m scripts.preview_book --slug test-princess --child-name "Софія"
    python -m scripts.preview_book --slug test-princess --child-name "Софія" --page 0
    python -m scripts.preview_book --slug test-princess --child-name "Софія" --output ./preview_output
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.book.manifest import BookManifest, TextLayer


# Simplified render function (no S3, local files only)
async def render_text_layers_local(
    bg_img: Image.Image,
    layers: List[TextLayer],
    template_vars: Dict[str, Any],
    output_px: int,
    fonts_dir: Optional[Path] = None,
) -> Image.Image:
    """Render text layers using Playwright (local version without S3)."""

    if not layers:
        return bg_img

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: playwright not installed. Run: pip install playwright && playwright install chromium")
        return bg_img

    import base64
    import html
    import io

    def pil_to_data_uri(img: Image.Image) -> str:
        if img.size != (output_px, output_px):
            img = img.resize((output_px, output_px), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    def font_to_data_uri(font_path: Path) -> str:
        if not font_path.exists():
            return ""
        data = font_path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        mime = "font/ttf" if font_path.suffix.lower() == ".ttf" else "font/otf"
        return f"data:{mime};base64,{b64}"

    def build_html(bg_uri: str, font_uri: str, text: str, style: Dict[str, Any]) -> str:
        target = style.get("target_size", output_px)
        font_size = style.get("font_size", 32)
        color = style.get("color", "#ffffff")
        text_align = style.get("text_align", "center")
        top = style.get("top", 100)
        margin_left = style.get("margin_left", 0)
        box_w = style.get("box_w", 800)
        box_h = style.get("box_h", 400)
        stroke_width = style.get("stroke_width", 0)
        stroke_color = style.get("stroke_color", "#000000")
        font_family = style.get("font_family", "'Comic Sans MS', sans-serif")

        safe_text = html.escape(text).replace("\n", "<br>")

        font_face = ""
        if font_uri:
            font_face = f"""
            @font-face {{
                font-family: 'CustomFont';
                src: url('{font_uri}');
            }}
            """
            font_family = f"'CustomFont', {font_family}"

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
                    width: {target}px;
                    height: {target}px;
                    overflow: hidden;
                }}
                body {{
                    background: url('{bg_uri}') center center / cover no-repeat;
                    display: flex;
                    justify-content: center;
                    align-items: flex-start;
                }}
                .text {{
                    position: relative;
                    margin-top: {top}px;
                    margin-left: {margin_left}px;
                    width: {box_w}px;
                    height: {box_h}px;
                }}
                .fill {{
                    color: {color};
                    font-family: {font_family};
                    font-size: {font_size}px;
                    font-weight: 600;
                    line-height: 1.3;
                    text-align: {text_align};
                    white-space: pre-line;
                    -webkit-text-stroke: {stroke_width}px {stroke_color};
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
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

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])

        cur = bg_img
        for layer in layers:
            # Render template
            template = layer.text_template or layer.text_key or ""
            try:
                text = template.format_map(template_vars)
            except Exception as e:
                print(f"  Warning: template error: {e}")
                text = template

            style = dict(layer.style or {})
            style["target_size"] = output_px

            bg_uri = pil_to_data_uri(cur)

            # Try to load font
            font_uri = ""
            if layer.font_uri and fonts_dir:
                font_name = Path(layer.font_uri).name
                font_path = fonts_dir / font_name
                if font_path.exists():
                    font_uri = font_to_data_uri(font_path)

            html_doc = build_html(bg_uri, font_uri, text, style)

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


def load_manifest(manifest_path: Path) -> BookManifest:
    """Load manifest from local JSON file."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("slug", manifest_path.parent.name)
    return BookManifest.model_validate(data)


def load_local_image(base_uri: str, templates_dir: Path) -> Image.Image:
    """Load image from local templates directory."""
    # base_uri is like "templates/test-princess/pages/page_00.png"
    # We need to resolve it relative to templates_dir parent

    if base_uri.startswith("templates/"):
        rel_path = base_uri[len("templates/"):]
    else:
        rel_path = base_uri

    img_path = templates_dir / rel_path

    if not img_path.exists():
        # Try alternative paths
        alt_paths = [
            templates_dir.parent / base_uri,
            templates_dir / base_uri,
            Path(base_uri),
        ]
        for alt in alt_paths:
            if alt.exists():
                img_path = alt
                break

    if not img_path.exists():
        print(f"  Warning: Image not found: {img_path}")
        # Return blank image
        return Image.new("RGB", (2551, 2551), (200, 200, 200))

    return Image.open(img_path).convert("RGB")


async def preview_page(
    manifest: BookManifest,
    page_num: int,
    templates_dir: Path,
    template_vars: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Image.Image:
    """Preview a single page."""

    spec = manifest.page_by_num(page_num)
    if not spec:
        print(f"  Error: Page {page_num} not found in manifest")
        return Image.new("RGB", (2551, 2551), (100, 100, 100))

    print(f"  Loading page {page_num}: {spec.base_uri}")

    # Load base image
    slug_dir = templates_dir / manifest.slug
    bg_img = load_local_image(spec.base_uri, slug_dir)

    # Resize to output size
    output_px = manifest.output.page_size_px
    if bg_img.size != (output_px, output_px):
        bg_img = bg_img.resize((output_px, output_px), Image.Resampling.LANCZOS)

    # Render text layers
    if spec.text_layers:
        print(f"  Rendering {len(spec.text_layers)} text layer(s)...")
        fonts_dir = slug_dir / "fonts"
        bg_img = await render_text_layers_local(
            bg_img,
            spec.text_layers,
            template_vars,
            output_px,
            fonts_dir if fonts_dir.exists() else None,
        )

    # Save if output_dir provided
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"page_{page_num:02d}.png"
        bg_img.save(out_path, "PNG")
        print(f"  Saved: {out_path}")

    return bg_img


async def preview_book(
    slug: str,
    child_name: str,
    templates_dir: Path,
    output_dir: Optional[Path] = None,
    page_num: Optional[int] = None,
    child_age: int = 5,
) -> List[Image.Image]:
    """Preview entire book or single page."""

    manifest_path = templates_dir / slug / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: Manifest not found: {manifest_path}")
        return []

    print(f"Loading manifest: {manifest_path}")
    manifest = load_manifest(manifest_path)
    print(f"Book: {manifest.slug}, Pages: {len(manifest.pages)}, Size: {manifest.output.page_size_px}px")

    template_vars = {
        "child_name": child_name,
        "child_age": child_age,
        "child_gender": "girl",
    }

    images = []

    if page_num is not None:
        # Single page
        img = await preview_page(manifest, page_num, templates_dir, template_vars, output_dir)
        images.append(img)
    else:
        # All pages
        for spec in sorted(manifest.pages, key=lambda p: p.page_num):
            img = await preview_page(manifest, spec.page_num, templates_dir, template_vars, output_dir)
            images.append(img)

    return images


def create_pdf(images: List[Image.Image], output_path: Path):
    """Create PDF from images."""
    if not images:
        print("No images to create PDF")
        return

    # Convert to RGB if needed
    rgb_images = []
    for img in images:
        if img.mode != "RGB":
            img = img.convert("RGB")
        rgb_images.append(img)

    # Save as PDF
    rgb_images[0].save(
        output_path,
        "PDF",
        save_all=True,
        append_images=rgb_images[1:] if len(rgb_images) > 1 else [],
        resolution=300,
    )
    print(f"\n✅ PDF saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Preview book pages locally")
    parser.add_argument("--slug", required=True, help="Book slug (e.g., test-princess)")
    parser.add_argument("--child-name", required=True, help="Child name for personalization")
    parser.add_argument("--child-age", type=int, default=5, help="Child age (default: 5)")
    parser.add_argument("--page", type=int, help="Specific page number (optional)")
    parser.add_argument("--output", type=Path, help="Output directory for images")
    parser.add_argument("--pdf", type=Path, help="Output PDF path")
    parser.add_argument("--templates-dir", type=Path, help="Templates directory (default: ./templates)")

    args = parser.parse_args()

    # Determine templates directory
    if args.templates_dir:
        templates_dir = args.templates_dir
    else:
        # Try common locations
        candidates = [
            Path(__file__).parent.parent / "templates",
            Path("templates"),
            Path("backend/templates"),
        ]
        templates_dir = None
        for c in candidates:
            if c.exists():
                templates_dir = c
                break

        if not templates_dir:
            print("Error: Could not find templates directory. Use --templates-dir")
            sys.exit(1)

    print(f"\n📚 Preview Book: {args.slug}")
    print(f"👤 Child: {args.child_name}, Age: {args.child_age}")
    print(f"📁 Templates: {templates_dir}")
    print()

    # Run preview
    images = asyncio.run(preview_book(
        slug=args.slug,
        child_name=args.child_name,
        child_age=args.child_age,
        templates_dir=templates_dir,
        output_dir=args.output,
        page_num=args.page,
    ))

    # Create PDF if requested
    if args.pdf and images:
        create_pdf(images, args.pdf)
    elif not args.output and not args.pdf:
        print("\n💡 Tip: Use --output ./preview or --pdf book.pdf to save results")

    print(f"\n✅ Done! Processed {len(images)} page(s)")


if __name__ == "__main__":
    main()
