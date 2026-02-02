#!/usr/bin/env python3
"""
Тестовий скрипт для аналізу обличчя через Qwen2-VL
Запуск: python3 test_analyze_face.py [шлях_до_фото]
"""

import sys
import os
import json
import logging

# Налаштування логування
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Додаємо backend до path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from app.inference.vision_qwen import analyze_image_pil
from app.config import settings

def main():
    # Визначаємо шлях до фото
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    else:
        # За замовчуванням - останнє фото в assets
        assets_dir = os.path.join(os.path.dirname(__file__), "assets")
        files = sorted(os.listdir(assets_dir), key=lambda x: os.path.getmtime(os.path.join(assets_dir, x)), reverse=True)
        image_files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        if not image_files:
            print("❌ Немає фото в assets/")
            return
        image_path = os.path.join(assets_dir, image_files[0])
    
    print("=" * 60)
    print("🔍 АНАЛІЗ ОБЛИЧЧЯ")
    print("=" * 60)
    print(f"📷 Фото: {image_path}")
    
    # Завантажуємо зображення
    try:
        pil_image = Image.open(image_path)
        print(f"📐 Розмір: {pil_image.size}")
    except Exception as e:
        print(f"❌ Помилка завантаження: {e}")
        return
    
    # Запускаємо аналіз
    print("\n⏳ Запускаю аналіз (це може зайняти хвилину)...")
    print(f"🤖 Модель: {settings.QWEN_MODEL_ID}")
    
    try:
        result = analyze_image_pil(pil_image, settings.QWEN_MODEL_ID)
        
        print("\n" + "=" * 60)
        print("📊 РЕЗУЛЬТАТ:")
        print("=" * 60)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # Форматований вивід
        if result.get("face_detected"):
            print("\n✅ Обличчя знайдено!")
            print(f"   Стать: {result.get('gender')}")
            print(f"   Колір волосся: {result.get('hair_color')}")
            print(f"   Довжина волосся: {result.get('hair_length')}")
            print(f"   Стиль волосся: {result.get('hair_style')}")
            print(f"   Колір очей: {result.get('eyes_color')}")
            print(f"\n📝 Опис: {result.get('full_description')}")
        else:
            print("\n❌ Обличчя не знайдено")
            
    except Exception as e:
        print(f"❌ Помилка аналізу: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
