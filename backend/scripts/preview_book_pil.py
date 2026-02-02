#!/usr/bin/env python3
"""
Preview book pages using PIL (no Playwright needed).
Simpler text rendering but works on any Python version.

Usage:
    python3 -m scripts.preview_book_pil --slug test-princess --child-name "Софія"
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont


def load_manifest(manifest_path: Path) -> dict:
    """Load manifest from local JSON file."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data.setdefault("slug", manifest_path.parent.name)
    return data


def find_font(font_name: str = None, size: int = 32) -> ImageFont.FreeTypeFont:
    """Find a suitable font."""
    font_paths = [
        # macOS system fonts
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    for fp in font_paths:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue

    # Fallback to default
    try:
        return ImageFont.truetype("Arial", size)
    except Exception:
        return ImageFont.load_default()


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """Wrap text to fit within max_width."""
    lines = []
    for paragraph in text.split('\n'):
        if not paragraph:
            lines.append('')
            continue

        words = paragraph.split()
        if not words:
            lines.append('')
            continue

        current_line = words[0]
        for word in words[1:]:
            test_line = current_line + ' ' + word
            bbox = font.getbbox(test_line)
            if bbox[2] - bbox[0] <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word
        lines.append(current_line)

    return lines


def render_text_on_image(
    img: Image.Image,
    text: str,
    style: Dict[str, Any],
    output_px: int,
) -> Image.Image:
    """Render text on image using PIL."""

    # Get style settings
    font_size = int(style.get("font_size", 32))
    color = style.get("color", "#ffffff")
    text_align = style.get("text_align", "center")
    top = int(style.get("top", 100))
    margin_left = int(style.get("margin_left", 0))
    box_w = int(style.get("box_w", 800))
    box_h = int(style.get("box_h", 400))
    stroke_width = int(style.get("stroke_width", 0))
    stroke_color = style.get("stroke_color", "#000000")

    # Scale font size relative to output
    scale = output_px / 2551 if output_px != 2551 else 1.0
    font_size = int(font_size * scale)
    top = int(top * scale)
    margin_left = int(margin_left * scale)
    box_w = int(box_w * scale)
    box_h = int(box_h * scale)
    stroke_width = max(1, int(stroke_width * scale)) if stroke_width > 0 else 0

    # Make copy
    img = img.copy()
    draw = ImageDraw.Draw(img)

    # Load font
    font = find_font(size=font_size)

    # Wrap text
    lines = wrap_text(text, font, box_w)

    # Calculate text position
    x_center = (img.width // 2) + margin_left
    y = top

    line_height = int(font_size * 1.3)

    for line in lines:
        if not line:
            y += line_height // 2
            continue

        bbox = font.getbbox(line)
        text_width = bbox[2] - bbox[0]

        if text_align == "center":
            x = x_center - text_width // 2
        elif text_align == "right":
            x = x_center + box_w // 2 - text_width
        else:  # left
            x = x_center - box_w // 2

        # Draw stroke/outline
        if stroke_width > 0:
            for dx in range(-stroke_width, stroke_width + 1):
                for dy in range(-stroke_width, stroke_width + 1):
                    if dx != 0 or dy != 0:
                        draw.text((x + dx, y + dy), line, font=font, fill=stroke_color)

        # Draw main text
        draw.text((x, y), line, font=font, fill=color)

        y += line_height

    return img


def load_local_image(base_uri: str, templates_dir: Path, slug: str) -> Image.Image:
    """Load image from local templates directory."""

    # Try different path patterns
    if base_uri.startswith("templates/"):
        rel_path = base_uri[len("templates/"):]
    else:
        rel_path = base_uri

    candidates = [
        templates_dir / slug / "pages" / Path(rel_path).name,
        templates_dir / rel_path,
        templates_dir / slug / rel_path,
        templates_dir.parent / base_uri,
    ]

    for path in candidates:
        if path.exists():
            return Image.open(path).convert("RGB")

    print(f"  Warning: Image not found: {base_uri}")
    return Image.new("RGB", (2551, 2551), (200, 200, 200))


def preview_page(
    manifest: dict,
    page_spec: dict,
    templates_dir: Path,
    template_vars: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> Image.Image:
    """Preview a single page."""

    page_num = page_spec["page_num"]
    base_uri = page_spec["base_uri"]
    text_layers = page_spec.get("text_layers", [])

    print(f"  Page {page_num}: {Path(base_uri).name}")

    # Load base image
    slug = manifest["slug"]
    output_px = manifest.get("output", {}).get("page_size_px", 2551)

    img = load_local_image(base_uri, templates_dir, slug)

    # Resize to output size
    if img.size != (output_px, output_px):
        img = img.resize((output_px, output_px), Image.Resampling.LANCZOS)

    # Render text layers
    for layer in text_layers:
        template = layer.get("text_template") or layer.get("text_key") or ""
        try:
            text = template.format_map(template_vars)
        except Exception as e:
            print(f"    Warning: template error: {e}")
            text = template

        style = layer.get("style", {})
        img = render_text_on_image(img, text, style, output_px)
        print(f"    + Text: {text[:50]}...")

    # Save if output_dir provided
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"page_{page_num:02d}.png"
        img.save(out_path, "PNG")
        print(f"    Saved: {out_path.name}")

    return img


def create_pdf(images: List[Image.Image], output_path: Path):
    """Create PDF from images."""
    if not images:
        print("No images to create PDF")
        return

    rgb_images = [img.convert("RGB") for img in images]

    rgb_images[0].save(
        output_path,
        "PDF",
        save_all=True,
        append_images=rgb_images[1:] if len(rgb_images) > 1 else [],
        resolution=300,
    )
    print(f"\n✅ PDF saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Preview book pages with PIL")
    parser.add_argument("--slug", required=True, help="Book slug")
    parser.add_argument("--child-name", required=True, help="Child name")
    parser.add_argument("--child-age", type=int, default=5, help="Child age")
    parser.add_argument("--page", type=int, help="Specific page number")
    parser.add_argument("--output", type=Path, help="Output directory")
    parser.add_argument("--pdf", type=Path, help="Output PDF path")
    parser.add_argument("--templates-dir", type=Path, help="Templates directory")

    args = parser.parse_args()

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

    print(f"\n📚 Preview: {args.slug}")
    print(f"👤 Child: {args.child_name}, Age: {args.child_age}")
    print(f"📁 Templates: {templates_dir}\n")

    manifest = load_manifest(manifest_path)
    print(f"Book: {manifest['slug']}, Pages: {len(manifest['pages'])}")
    print()

    template_vars = {
        "child_name": args.child_name,
        "child_age": args.child_age,
        "child_gender": "girl",
    }

    images = []
    pages = sorted(manifest["pages"], key=lambda p: p["page_num"])

    if args.page is not None:
        pages = [p for p in pages if p["page_num"] == args.page]

    for page_spec in pages:
        img = preview_page(manifest, page_spec, templates_dir, template_vars, args.output)
        images.append(img)

    if args.pdf and images:
        create_pdf(images, args.pdf)

    print(f"\n✅ Done! {len(images)} page(s)")

    if not args.output and not args.pdf:
        print("💡 Use --output ./preview or --pdf book.pdf to save")


if __name__ == "__main__":
    main()
