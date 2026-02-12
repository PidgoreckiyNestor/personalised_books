#!/usr/bin/env python3
"""
Test face swap with ComfyUI.

Usage:
  python test_face_swap.py --child assets/child1.png --illustration templates/wonderland-book/pages/page_01_base.jpg
  python test_face_swap.py --child assets/child1.png --illustration templates/wonderland-book/pages/page_01_base.jpg --mask masks/mask_page_01.png
"""

import argparse
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from app.config import settings


def main():
    parser = argparse.ArgumentParser(description="Test ComfyUI face swap")
    parser.add_argument("--child", required=True, help="Path to child photo (source face)")
    parser.add_argument("--illustration", required=True, help="Path to illustration (target)")
    parser.add_argument("--mask", help="Path to mask (optional, will auto-generate if not provided)")
    parser.add_argument("--output", default="test_output/face_swap_result.png", help="Output path")
    parser.add_argument("--prompt", default="a young girl in a magical wonderland", help="Positive prompt")
    parser.add_argument("--negative", default="deformed, ugly, bad anatomy", help="Negative prompt")

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  ComfyUI Face Swap Test")
    print(f"{'='*60}")
    print(f"\n  ComfyUI URL: {settings.COMFY_BASE_URL}")

    # Load images
    print(f"\n  Loading images...")

    child_pil = Image.open(args.child).convert("RGB")
    print(f"    Child: {args.child} ({child_pil.size[0]}x{child_pil.size[1]})")

    illustration_pil = Image.open(args.illustration).convert("RGB")
    print(f"    Illustration: {args.illustration} ({illustration_pil.size[0]}x{illustration_pil.size[1]})")

    mask_pil = None
    if args.mask:
        mask_pil = Image.open(args.mask).convert("L")
        print(f"    Mask: {args.mask} ({mask_pil.size[0]}x{mask_pil.size[1]})")
    else:
        print(f"    No mask provided - will auto-generate")

    print(f"\n  Prompt: {args.prompt}")
    print(f"  Negative: {args.negative}")

    # Import here to avoid loading everything on --help
    from app.inference.comfy_runner import run_face_transfer_comfy_api

    print(f"\n  Starting face transfer...")
    print(f"  (this may take 30-120 seconds)")

    try:
        result = run_face_transfer_comfy_api(
            child_pil=child_pil,
            illustration_pil=illustration_pil,
            prompt=args.prompt,
            negative_prompt=args.negative,
            mask_pil=mask_pil,
        )

        # Save result
        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        result.save(args.output)
        print(f"\n  Success!")
        print(f"  Saved: {args.output}")

    except Exception as e:
        print(f"\n  Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print(f"\n{'='*60}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
