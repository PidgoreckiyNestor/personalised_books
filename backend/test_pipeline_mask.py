#!/usr/bin/env python3
"""
Тест створення маски через систему (comfy_runner._build_face_mask)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
import cv2
import numpy as np

# Спробуємо імпортувати MediaPipe
MEDIAPIPE_AVAILABLE = False
mp = None
try:
    import mediapipe
    mp = mediapipe
    # Перевіряємо який API доступний
    if hasattr(mp, 'solutions') and hasattr(mp.solutions, 'face_detection'):
        MEDIAPIPE_AVAILABLE = "solutions"
    elif hasattr(mp, 'tasks'):
        MEDIAPIPE_AVAILABLE = "tasks"
    print(f"✓ MediaPipe {mp.__version__} (API: {MEDIAPIPE_AVAILABLE or 'none'})")
except ImportError:
    print("⚠️  MediaPipe не встановлено. pip install mediapipe")


def detect_face_mediapipe(img_rgb: np.ndarray):
    """Детекція обличчя через MediaPipe - бере найкраще з усіх детекцій"""
    if not MEDIAPIPE_AVAILABLE:
        return None

    h, w = img_rgb.shape[:2]
    all_detections = []

    # Новий API (tasks) - MediaPipe 0.10+
    if MEDIAPIPE_AVAILABLE == "tasks":
        try:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision

            model_path = os.path.join(os.path.dirname(__file__), "models", "blaze_face_short_range.tflite")
            if not os.path.exists(model_path):
                print(f"      ⚠️ Model not found: {model_path}")
                return None

            # Низький поріг щоб знайти всі можливі обличчя
            base_options = python.BaseOptions(model_asset_path=model_path)
            options = vision.FaceDetectorOptions(
                base_options=base_options,
                min_detection_confidence=0.15  # Низький поріг
            )

            detector = vision.FaceDetector.create_from_options(options)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=img_rgb)
            results = detector.detect(mp_image)

            for det in results.detections:
                bbox = det.bounding_box
                x1, y1 = bbox.origin_x, bbox.origin_y
                x2, y2 = x1 + bbox.width, y1 + bbox.height
                conf = det.categories[0].score
                face_area = bbox.width * bbox.height

                # Фільтруємо занадто малі або великі детекції
                img_area = w * h
                area_ratio = face_area / img_area

                if 0.005 < area_ratio < 0.5:  # Обличчя від 0.5% до 50% зображення
                    all_detections.append({
                        "bbox": (int(x1), int(y1), int(x2), int(y2)),
                        "conf": conf,
                        "area": face_area,
                        "area_ratio": area_ratio
                    })

        except Exception as e:
            print(f"      ⚠️ MediaPipe Tasks error: {e}")

    # Старий API (solutions)
    if MEDIAPIPE_AVAILABLE == "solutions":
        try:
            mp_face = mp.solutions.face_detection
            with mp_face.FaceDetection(model_selection=1, min_detection_confidence=0.15) as detector:
                results = detector.process(img_rgb)
                for det in (results.detections or []):
                    bbox = det.location_data.relative_bounding_box
                    x1 = int(bbox.xmin * w)
                    y1 = int(bbox.ymin * h)
                    x2 = int((bbox.xmin + bbox.width) * w)
                    y2 = int((bbox.ymin + bbox.height) * h)
                    conf = det.score[0]
                    face_area = (x2 - x1) * (y2 - y1)
                    area_ratio = face_area / (w * h)

                    if 0.005 < area_ratio < 0.5:
                        all_detections.append({
                            "bbox": (x1, y1, x2, y2),
                            "conf": conf,
                            "area": face_area,
                            "area_ratio": area_ratio
                        })
        except Exception as e:
            print(f"      ⚠️ MediaPipe Solutions error: {e}")

    if not all_detections:
        return None

    # Сортуємо за confidence (головний критерій)
    # При однаковому confidence - беремо з оптимальним розміром (5-15% зображення)
    for d in all_detections:
        # Оптимальний розмір обличчя: 5-15% зображення
        optimal_ratio = 0.10
        size_penalty = abs(d["area_ratio"] - optimal_ratio) / optimal_ratio
        # Скор: confidence з невеликим штрафом за неоптимальний розмір
        d["score"] = d["conf"] - (size_penalty * 0.1)

    # Фільтруємо дуже низький confidence
    good_detections = [d for d in all_detections if d["conf"] >= 0.4]
    if not good_detections:
        good_detections = all_detections  # Якщо всі погані - беремо що є

    best = max(good_detections, key=lambda d: d["score"])

    # Debug: показати всі детекції
    if len(all_detections) > 1:
        print(f"      📊 Всі детекції:")
        for i, d in enumerate(sorted(all_detections, key=lambda x: -x["conf"])):
            marker = "→" if d == best else " "
            print(f"         {marker} [{i+1}] conf={d['conf']:.2f}, area={d['area_ratio']*100:.1f}%, bbox={d['bbox']}")
    else:
        print(f"      📊 conf={best['conf']:.2f}, area={best['area_ratio']*100:.1f}%")

    return best["bbox"], best["conf"]


def detect_face_haar(gray: np.ndarray):
    """Fallback детекція через Haar Cascades"""
    cascades_to_try = [
        ("haarcascade_frontalface_alt2.xml", 1.05, 3),
        ("haarcascade_frontalface_default.xml", 1.1, 4),
        ("haarcascade_frontalface_alt.xml", 1.1, 3),
        ("haarcascade_profileface.xml", 1.1, 3),
    ]

    for cascade_name, scale, neighbors in cascades_to_try:
        try:
            cascade_path = cv2.data.haarcascades + cascade_name
            cascade = cv2.CascadeClassifier(cascade_path)

            for min_size in [(30, 30), (50, 50), (80, 80)]:
                dets = cascade.detectMultiScale(
                    gray, scaleFactor=scale, minNeighbors=neighbors,
                    minSize=min_size, flags=cv2.CASCADE_SCALE_IMAGE
                )
                if len(dets) > 0:
                    dets = sorted(dets, key=lambda r: r[2] * r[3], reverse=True)
                    x, y, bw, bh = dets[0]
                    return (int(x), int(y), int(x + bw), int(y + bh)), cascade_name
        except Exception:
            continue
    return None


def _build_face_mask(pil_img: Image.Image):
    """
    КОПІЯ з comfy_runner.py - створення маски для ілюстрації
    """
    try:
        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        h, w = img_np.shape[:2]

        x1 = y1 = x2 = y2 = None
        detection_method = None
        confidence = None

        # 1. MediaPipe (найкращий для реальних фото)
        mp_result = detect_face_mediapipe(img_np)
        if mp_result:
            (x1, y1, x2, y2), confidence = mp_result
            detection_method = f"MediaPipe (conf={confidence:.2f})"

            # Якщо confidence низький - можливо ілюстрація, пробуємо Haar
            if confidence < 0.5:
                print(f"      ⚠️ Low confidence ({confidence:.2f}), trying Haar...")
                haar_result = detect_face_haar(gray)
                if haar_result:
                    h_bbox, h_method = haar_result
                    h_area = (h_bbox[2] - h_bbox[0]) * (h_bbox[3] - h_bbox[1])
                    mp_area = (x2 - x1) * (y2 - y1)
                    # Якщо Haar знайшов менше обличчя - воно скоріш за все правильніше
                    if h_area < mp_area * 0.7:
                        (x1, y1, x2, y2) = h_bbox
                        detection_method = f"Haar (MediaPipe low conf)"
                        print(f"      ✓ Haar обрав менший bbox")

        # 2. Fallback на Haar якщо MediaPipe нічого не знайшов
        if x1 is None:
            haar_result = detect_face_haar(gray)
            if haar_result:
                (x1, y1, x2, y2), cascade_name = haar_result
                detection_method = f"Haar: {cascade_name}"

        if x1 is None or y1 is None or x2 is None or y2 is None or x2 <= x1 or y2 <= y1:
            # Fallback: centered ellipse
            cx = w // 2
            cy = int(h * 0.45)
            ax = max(1, int(w * 0.18))
            ay = max(1, int(h * 0.22))
            detection = {"detected": False, "fallback": True, "cx": cx, "cy": cy, "ax": ax, "ay": ay}
        else:
            bw = x2 - x1
            bh = y2 - y1
            cx = x1 + bw // 2 - int(bw * 0.15)  # Ще лівіше на 15%
            cy = y1 + int(bh * 0.20)
            ax = max(1, int(bw * 0.9))  # Ширше для волосся зліва
            ay = max(1, int(bh * 1.1))
            detection = {"detected": True, "bbox": (x1, y1, x2, y2), "cx": cx, "cy": cy, "ax": ax, "ay": ay, "method": detection_method}

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(mask, (int(cx), int(cy)), (int(ax), int(ay)), 0, 0, 360, 255, -1)

        sigma = max(8, int(min(w, h) * 0.03))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)

        return Image.fromarray(mask), detection

    except Exception as e:
        print(f"❌ Error: {e}")
        return None, {"error": str(e)}


def create_red_mask(mask_gray: Image.Image) -> Image.Image:
    """Конвертує grayscale маску в RED-only формат для ComfyUI"""
    mask_np = np.array(mask_gray)
    h, w = mask_np.shape
    mask_red = np.zeros((h, w, 3), dtype=np.uint8)
    mask_red[:, :, 0] = mask_np  # Тільки червоний канал
    return Image.fromarray(mask_red)


def create_visualization(original: Image.Image, mask: Image.Image) -> Image.Image:
    """Створює візуалізацію маски поверх оригіналу"""
    viz = original.convert("RGBA")
    mask_np = np.array(mask)
    h, w = mask_np.shape
    overlay = np.zeros((h, w, 4), dtype=np.uint8)
    overlay[:, :, 0] = mask_np  # Red
    overlay[:, :, 3] = (mask_np * 0.6).astype(np.uint8)  # Alpha
    return Image.alpha_composite(viz, Image.fromarray(overlay, 'RGBA'))


def main():
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    output_dir = os.path.join(os.path.dirname(__file__), "test_output")
    os.makedirs(output_dir, exist_ok=True)
    
    # Знаходимо ілюстрації (не фото людей)
    files = os.listdir(assets_dir)
    illustrations = [f for f in files if f.lower().startswith('screenshot') and f.endswith('.png')]
    
    if not illustrations:
        print("❌ Немає ілюстрацій в assets/ (файли Screenshot*.png)")
        return
    
    print("=" * 60)
    print("🎭 ТЕСТ СТВОРЕННЯ МАСКИ")
    print("=" * 60)
    
    for filename in illustrations:
        img_path = os.path.join(assets_dir, filename)
        print(f"\n📷 {filename}")
        
        # Завантажуємо
        pil_img = Image.open(img_path)
        print(f"   Розмір: {pil_img.size}")
        
        # Створюємо маску
        mask, detection = _build_face_mask(pil_img)
        
        if mask is None:
            print(f"   ❌ Помилка створення маски")
            continue
        
        if detection.get("detected"):
            print(f"   ✅ Обличчя знайдено!")
            print(f"      Bbox: {detection.get('bbox')}")
            print(f"      Method: {detection.get('method')}")
        else:
            print(f"   ⚠️  Fallback (обличчя не знайдено)")
        
        print(f"      Center: ({detection['cx']}, {detection['cy']})")
        print(f"      Axes: ({detection['ax']}, {detection['ay']})")
        
        # Зберігаємо результати
        base = os.path.splitext(filename)[0]
        
        # Grayscale маска
        mask.save(os.path.join(output_dir, f"{base}_mask_gray.png"))
        
        # RED-only маска для ComfyUI
        mask_red = create_red_mask(mask)
        mask_red.save(os.path.join(output_dir, f"{base}_mask_RED.png"))
        
        # Візуалізація
        viz = create_visualization(pil_img, mask)
        viz.save(os.path.join(output_dir, f"{base}_visualization.png"))
        
        print(f"   💾 Збережено в test_output/")
    
    print("\n" + "=" * 60)
    print(f"📂 Результати: {output_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
