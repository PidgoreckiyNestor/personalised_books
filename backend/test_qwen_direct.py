#!/usr/bin/env python3
"""
Прямий тест Qwen2-VL (без InsightFace)
"""
import sys
import os
import json
import logging

logging.basicConfig(level=logging.INFO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from app.config import settings

def analyze_with_qwen_direct(pil_image, model_id: str):
    """Аналіз напряму через Qwen без InsightFace"""
    import torch
    from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
    from qwen_vl_utils import process_vision_info
    from app.inference import qwen_json_guard
    
    print("📦 Завантажую модель Qwen2-VL...")
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        model_id,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True
    )
    processor = AutoProcessor.from_pretrained(model_id)
    
    PROMPT = """Look at this image and describe the person's face.
Return a JSON object with these fields:
- face_detected: true/false
- gender: boy/girl/man/woman
- hair_color: describe the hair color
- hair_length: short, medium, or long
- hair_style: straight, curly, wavy, etc.
- eyes_color: describe the eye color
- full_description: brief description of appearance

Return ONLY valid JSON, no other text."""

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": pil_image},
                {"type": "text", "text": PROMPT}
            ]
        }
    ]
    
    print("🔍 Аналізую зображення...")
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(text=[text], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt")
    inputs = inputs.to(model.device)
    
    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=200, do_sample=False)
    
    generated_ids_trimmed = [out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)]
    output_text = processor.batch_decode(generated_ids_trimmed, skip_special_tokens=True)[0]
    
    print(f"\n📝 Raw output:\n{output_text}\n")
    
    cleaned = qwen_json_guard.extract_json(output_text)
    try:
        return json.loads(cleaned)
    except:
        return {"raw_output": output_text, "face_detected": False}

def main():
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    files = sorted(os.listdir(assets_dir), key=lambda x: os.path.getmtime(os.path.join(assets_dir, x)), reverse=True)
    image_files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
    elif image_files:
        image_path = os.path.join(assets_dir, image_files[0])
    else:
        print("❌ Немає фото")
        return
    
    print("=" * 60)
    print(f"📷 Фото: {os.path.basename(image_path)}")
    
    pil_image = Image.open(image_path)
    print(f"📐 Розмір: {pil_image.size}")
    
    result = analyze_with_qwen_direct(pil_image, settings.QWEN_MODEL_ID)
    
    print("=" * 60)
    print("📊 РЕЗУЛЬТАТ:")
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
  