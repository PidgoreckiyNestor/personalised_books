#!/usr/bin/env python3
"""
Create face masks for ComfyUI face swap.
Output: RED on BLACK with soft edges (Gaussian blur).

Usage:
  python scripts/create_mask.py --page 1 --cx 542 --cy 500 --rx 150 --ry 180
  python scripts/create_mask.py --page 1 --cx 542 --cy 500 --rx 150 --ry 180 --preview
  python scripts/create_mask.py --batch masks.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# Default image size
DEFAULT_SIZE = 1080
DEFAULT_BLUR = 25


def create_mask(
    cx: int,
    cy: int,
    rx: int,
    ry: int,
    width: int = DEFAULT_SIZE,
    height: int = DEFAULT_SIZE,
    blur: int = DEFAULT_BLUR,
    output_format: str = "red"  # "red" for RGB red-on-black, "gray" for grayscale
) -> Image.Image:
    """
    Create an elliptical mask with soft edges.

    Args:
        cx, cy: Center coordinates
        rx, ry: Radius X and Y (semi-axes)
        width, height: Image dimensions
        blur: Gaussian blur sigma
        output_format: "red" for red-on-black RGB, "gray" for grayscale

    Returns:
        PIL Image with the mask
    """
    # Create black image
    mask = np.zeros((height, width), dtype=np.uint8)

    # Draw white ellipse
    cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 255, -1)

    # Apply Gaussian blur for soft edges
    if blur > 0:
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=blur, sigmaY=blur)

    if output_format == "red":
        # Convert to RGB with red channel only
        rgb = np.zeros((height, width, 3), dtype=np.uint8)
        rgb[:, :, 0] = mask  # Red channel
        return Image.fromarray(rgb, mode="RGB")
    else:
        return Image.fromarray(mask, mode="L")


def create_preview(
    original_path: str,
    mask: Image.Image,
    output_path: str = None
) -> Image.Image:
    """Create a preview showing mask overlay on original image."""
    original = Image.open(original_path).convert("RGBA")

    # Resize mask to match original if needed
    if mask.size != original.size:
        mask = mask.resize(original.size, Image.Resampling.LANCZOS)

    # Create red overlay
    if mask.mode == "RGB":
        mask_gray = mask.split()[0]  # Get red channel
    else:
        mask_gray = mask

    overlay = Image.new("RGBA", original.size, (255, 0, 0, 0))
    overlay.putalpha(mask_gray)

    # Composite
    result = Image.alpha_composite(original, overlay)

    if output_path:
        result.save(output_path)

    return result


def get_page_path(page_num: int, template_dir: str = "templates/wonderland-book") -> str:
    """Get the path to a page image."""
    pages_dir = Path(template_dir) / "pages"

    # Try different extensions
    for ext in [".png", ".jpg", ".jpeg"]:
        path = pages_dir / f"page_{page_num:02d}_base{ext}"
        if path.exists():
            return str(path)

    return None


def main():
    parser = argparse.ArgumentParser(description="Create face masks for ComfyUI")
    parser.add_argument("--page", type=int, help="Page number (e.g., 1 for page_01)")
    parser.add_argument("--cx", type=int, help="Center X coordinate")
    parser.add_argument("--cy", type=int, help="Center Y coordinate")
    parser.add_argument("--rx", type=int, help="Radius X (horizontal)")
    parser.add_argument("--ry", type=int, help="Radius Y (vertical)")
    parser.add_argument("--blur", type=int, default=DEFAULT_BLUR, help=f"Blur sigma (default: {DEFAULT_BLUR})")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE, help=f"Image size (default: {DEFAULT_SIZE})")
    parser.add_argument("--preview", action="store_true", help="Generate preview overlay")
    parser.add_argument("--gray", action="store_true", help="Output grayscale instead of red")
    parser.add_argument("--batch", type=str, help="JSON file with batch mask definitions")
    parser.add_argument("--output-dir", type=str, default="templates/wonderland-book/masks",
                        help="Output directory for masks")
    parser.add_argument("--template-dir", type=str, default="templates/wonderland-book",
                        help="Template directory")

    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    masks_to_create = []

    if args.batch:
        # Load batch definitions from JSON
        with open(args.batch, "r") as f:
            batch_data = json.load(f)
        masks_to_create = batch_data.get("masks", batch_data)
    elif args.page is not None and args.cx and args.cy and args.rx and args.ry:
        masks_to_create = [{
            "page": args.page,
            "cx": args.cx,
            "cy": args.cy,
            "rx": args.rx,
            "ry": args.ry,
            "blur": args.blur
        }]
    else:
        parser.print_help()
        print("\nExample:")
        print("  python scripts/create_mask.py --page 1 --cx 542 --cy 500 --rx 150 --ry 180 --preview")
        return

    output_format = "gray" if args.gray else "red"

    for mask_def in masks_to_create:
        page_num = mask_def["page"]
        cx = mask_def["cx"]
        cy = mask_def["cy"]
        rx = mask_def["rx"]
        ry = mask_def["ry"]
        blur = mask_def.get("blur", args.blur)

        print(f"\nCreating mask for page {page_num:02d}...")
        print(f"  Center: ({cx}, {cy})")
        print(f"  Radius: ({rx}, {ry})")
        print(f"  Blur: {blur}")

        # Create mask
        mask = create_mask(cx, cy, rx, ry, args.size, args.size, blur, output_format)

        # Determine output filename
        # Find original to match extension
        page_path = get_page_path(page_num, args.template_dir)
        if page_path:
            base_name = os.path.basename(page_path)
            root, ext = os.path.splitext(base_name)
            mask_filename = f"mask_{root}.png"
        else:
            mask_filename = f"mask_page_{page_num:02d}_base.png"

        mask_path = os.path.join(args.output_dir, mask_filename)
        mask.save(mask_path)
        print(f"  Saved: {mask_path}")

        # Generate preview if requested
        if args.preview and page_path:
            preview_filename = f"preview_{page_num:02d}.png"
            preview_path = os.path.join(args.output_dir, preview_filename)
            create_preview(page_path, mask, preview_path)
            print(f"  Preview: {preview_path}")

    print(f"\nDone! {len(masks_to_create)} mask(s) created in {args.output_dir}")


if __name__ == "__main__":
    main()
