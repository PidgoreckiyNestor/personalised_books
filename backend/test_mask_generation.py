#!/usr/bin/env python3
"""
Test script for face mask generation.
Uses the EXACT same logic as the production pipeline (comfy_runner.py).
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import time
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _build_face_mask(pil_img: Image.Image) -> Image.Image:
    """
    EXACT COPY from comfy_runner.py (lines 414-483)
    Build a grayscale face mask (L) for an illustration image.
    """
    try:
        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        h, w = img_np.shape[:2]

        x1 = y1 = x2 = y2 = None
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(cascade_path)
            dets = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(dets) > 0:
                dets = sorted(dets, key=lambda r: r[2] * r[3], reverse=True)
                x, y, bw, bh = dets[0]
                x1, y1, x2, y2 = int(x), int(y), int(x + bw), int(y + bh)
        except Exception:
            x1 = y1 = x2 = y2 = None

        if x1 is None or y1 is None or x2 is None or y2 is None or x2 <= x1 or y2 <= y1:
            # Fallback: centered ellipse in the upper half of the page.
            cx = w // 2
            cy = int(h * 0.45)
            ax = max(1, int(w * 0.18))
            ay = max(1, int(h * 0.22))
            detection_info = {
                "detected": False,
                "fallback": True,
                "cx": cx, "cy": cy, "ax": ax, "ay": ay
            }
        else:
            bw = x2 - x1
            bh = y2 - y1
            cx = x1 + bw // 2
            cy = y1 + int(bh * 0.55)
            ax = max(1, int(bw * 0.8))
            ay = max(1, int(bh * 1.1))
            detection_info = {
                "detected": True,
                "fallback": False,
                "bbox": (x1, y1, x2, y2),
                "bbox_size": (bw, bh),
                "cx": cx, "cy": cy, "ax": ax, "ay": ay
            }

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(mask, (int(cx), int(cy)), (int(ax), int(ay)), 0, 0, 360, 255, -1)

        # Blur radius proportional to image size; tuned for 850px previews and larger.
        sigma = max(8, int(min(w, h) * 0.03))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)

        return Image.fromarray(mask), detection_info

    except Exception as e:
        print(f"  ❌ OpenCV failed: {e}")
        # PIL fallback
        w, h = pil_img.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        cx = w // 2
        cy = int(h * 0.45)
        ax = max(1, int(w * 0.18))
        ay = max(1, int(h * 0.22))
        draw.ellipse((cx - ax, cy - ay, cx + ax, cy + ay), fill=255)
        radius = max(2, int(min(w, h) * 0.03))
        return mask.filter(ImageFilter.GaussianBlur(radius=radius)), {
            "detected": False,
            "fallback": True,
            "pil_fallback": True
        }


def create_visualization(original: Image.Image, mask: Image.Image, detection_info: dict) -> Image.Image:
    """Create a side-by-side visualization with detection info."""
    # Resize if too large
    max_size = 600
    scale = 1.0
    if original.width > max_size or original.height > max_size:
        scale = max_size / max(original.width, original.height)
        new_size = (int(original.width * scale), int(original.height * scale))
        original = original.resize(new_size, Image.Resampling.LANCZOS)
        mask = mask.resize(new_size, Image.Resampling.LANCZOS)

    w, h = original.size

    # Create canvas for 3 images side by side
    canvas = Image.new("RGB", (w * 3, h), (40, 40, 40))

    # 1. Original image with detection box
    orig_with_box = original.convert("RGB").copy()
    if detection_info.get("detected") and detection_info.get("bbox"):
        from PIL import ImageDraw as ID
        draw = ID.Draw(orig_with_box)
        x1, y1, x2, y2 = detection_info["bbox"]
        # Scale bbox
        x1, y1, x2, y2 = int(x1*scale), int(y1*scale), int(x2*scale), int(y2*scale)
        draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=2)
        # Draw center point
        cx, cy = int(detection_info["cx"]*scale), int(detection_info["cy"]*scale)
        draw.ellipse([cx-5, cy-5, cx+5, cy+5], fill=(255, 0, 0))
    canvas.paste(orig_with_box, (0, 0))

    # 2. Mask as grayscale
    mask_rgb = Image.merge("RGB", (mask, mask, mask))
    canvas.paste(mask_rgb, (w, 0))

    # 3. Overlay - mask applied to original
    original_rgba = original.convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (255, 0, 0, 180))
    result = Image.composite(overlay, original_rgba, mask)
    canvas.paste(result.convert("RGB"), (w * 2, 0))

    return canvas


def test_mask_generation(image_path: str, output_dir: str = "test_output"):
    """Test mask generation on a single image using PIPELINE logic."""
    print(f"\n{'='*60}")
    print(f"Testing: {os.path.basename(image_path)}")
    print('='*60)

    # Load image
    try:
        img = Image.open(image_path)
        print(f"  📷 Image size: {img.size[0]}x{img.size[1]}")
    except Exception as e:
        print(f"  ❌ Failed to load image: {e}")
        return None

    # Generate mask using PIPELINE logic
    start_time = time.time()
    mask, detection_info = _build_face_mask(img)
    elapsed = (time.time() - start_time) * 1000

    # Print detection results
    if detection_info.get("detected"):
        bbox = detection_info.get("bbox", (0,0,0,0))
        bbox_size = detection_info.get("bbox_size", (0,0))
        print(f"  ✅ FACE DETECTED")
        print(f"     Bbox: ({bbox[0]}, {bbox[1]}) → ({bbox[2]}, {bbox[3]})")
        print(f"     Bbox size: {bbox_size[0]}x{bbox_size[1]} px")
        print(f"     Mask center: ({detection_info['cx']}, {detection_info['cy']})")
        print(f"     Mask axes: ({detection_info['ax']}, {detection_info['ay']})")
    else:
        print(f"  ⚠️  NO FACE DETECTED - using FALLBACK")
        print(f"     Fallback center: ({detection_info['cx']}, {detection_info['cy']})")
        print(f"     Fallback axes: ({detection_info['ax']}, {detection_info['ay']})")

    print(f"  ⏱️  Time: {elapsed:.1f} ms")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Save mask
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    mask_path = os.path.join(output_dir, f"{base_name}_mask.png")
    mask.save(mask_path)
    print(f"  💾 Mask: {mask_path}")

    # Save visualization
    viz = create_visualization(img, mask, detection_info)
    viz_path = os.path.join(output_dir, f"{base_name}_visualization.png")
    viz.save(viz_path)
    print(f"  💾 Visualization: {viz_path}")

    return {
        "image": os.path.basename(image_path),
        "detected": detection_info.get("detected", False),
        "time_ms": elapsed,
        "mask_path": mask_path,
        "viz_path": viz_path,
        "detection_info": detection_info
    }


def main():
    print("\n" + "="*60)
    print("  🎭 Face Mask Generation Test (PIPELINE LOGIC)")
    print("="*60)
    print("\n  Using exact same code as comfy_runner.py _build_face_mask()")

    # Test images
    test_images = []

    # Check command line arguments
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if os.path.exists(arg):
                test_images.append(arg)

    # Default: use images from assets folder
    if not test_images:
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")
        if os.path.exists(assets_dir):
            for f in sorted(os.listdir(assets_dir)):
                if f.lower().endswith(('.png', '.jpg', '.jpeg')) and not f.startswith('mask_'):
                    test_images.append(os.path.join(assets_dir, f))

    if not test_images:
        print("\n❌ No test images found!")
        print("   Usage: python test_mask_generation.py [image1.png] [image2.jpg] ...")
        print("   Or place images in backend/assets/ folder")
        return

    print(f"\n📁 Found {len(test_images)} test image(s)")

    output_dir = os.path.join(os.path.dirname(__file__), "test_output")

    results = []
    for img_path in test_images:
        result = test_mask_generation(img_path, output_dir)
        if result:
            results.append(result)

    # Summary
    print("\n" + "="*60)
    print("  📊 SUMMARY")
    print("="*60)

    detected_count = sum(1 for r in results if r["detected"])
    total_count = len(results)
    accuracy = (detected_count / total_count * 100) if total_count > 0 else 0

    print(f"\n  Total images:     {total_count}")
    print(f"  Faces detected:   {detected_count}")
    print(f"  Fallback used:    {total_count - detected_count}")
    print(f"  Detection rate:   {accuracy:.1f}%")

    print(f"\n  {'Image':<45} {'Detected':<10} {'Time':<10}")
    print(f"  {'-'*45} {'-'*10} {'-'*10}")
    for r in results:
        status = "✅ Yes" if r["detected"] else "❌ No"
        print(f"  {r['image'][:44]:<45} {status:<10} {r['time_ms']:.0f} ms")

    print(f"\n  📂 Results in: {output_dir}")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
