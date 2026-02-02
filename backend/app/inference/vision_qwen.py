import json
import logging
import platform
from PIL import Image
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import torch
from . import qwen_json_guard

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 1024  # Max dimension for Qwen analysis (to avoid OOM)


def _resize_for_analysis(pil_image: Image.Image) -> Image.Image:
    """Resize image if too large for GPU memory"""
    w, h = pil_image.size
    if max(w, h) <= MAX_IMAGE_SIZE:
        return pil_image

    if w > h:
        new_w = MAX_IMAGE_SIZE
        new_h = int(h * MAX_IMAGE_SIZE / w)
    else:
        new_h = MAX_IMAGE_SIZE
        new_w = int(w * MAX_IMAGE_SIZE / h)

    logger.info(f"Resizing image from {w}x{h} to {new_w}x{new_h} for analysis")
    return pil_image.resize((new_w, new_h), Image.LANCZOS)

_model = None
_processor = None


def _is_apple_silicon() -> bool:
    """Check if running on Apple Silicon (M1/M2/M3)"""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _get_model(model_id: str):
    global _model, _processor
    if _model is None:
        if _is_apple_silicon():
            # Apple Silicon (M1/M2/M3) - use MPS without quantization
            logger.info("Detected Apple Silicon, using MPS backend without quantization")
            _model = Qwen2VLForConditionalGeneration.from_pretrained(
                model_id,
                torch_dtype=torch.float16,
                device_map="auto",
                low_cpu_mem_usage=True
            )
        else:
            # NVIDIA GPU - use bitsandbytes 4-bit quantization
            try:
                from transformers import BitsAndBytesConfig
                logger.info("Using CUDA with 4-bit quantization")
                quantization_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4"
                )
                _model = Qwen2VLForConditionalGeneration.from_pretrained(
                    model_id,
                    quantization_config=quantization_config,
                    device_map="auto",
                    low_cpu_mem_usage=True
                )
            except ImportError:
                # Fallback without quantization
                logger.warning("bitsandbytes not available, loading without quantization")
                _model = Qwen2VLForConditionalGeneration.from_pretrained(
                    model_id,
                    torch_dtype=torch.float16,
                    device_map="auto",
                    low_cpu_mem_usage=True
                )
        _processor = AutoProcessor.from_pretrained(model_id)
    return _model, _processor

SYSTEM_RULE = (
    "You are a vision assistant specialized in analyzing children's faces. "
    "Look carefully at the image to detect human faces, especially children. "
    "Return ONLY valid JSON with these exact keys: "
    "face_detected, full_description, hair_color, eyes_color, gender, hair_length, hair_style."
)

USER_PROMPT = (
    "This image has already been processed by face detection software. "
    "I need you to analyze the facial features of the detected face only. "
    "Provide detailed analysis in JSON format:\n"
    "{\"face_detected\":true,\"full_description\":\"[detailed description]\","
    "\"hair_color\":\"[color]\",\"eyes_color\":\"[color]\",\"gender\":\"[boy/girl]\",\"hair_length\":\"[short/medium/long]\",\"hair_style\":\"[straight/curly/wavy/braided/etc]\"}\n"
    "Be very specific about colors and features. If uncertain about any detail, use 'unknown'."
)

def _try_insightface_fallback(pil_image):
    """Fallback: use InsightFace to detect and crop face if Qwen fails"""
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
        
        try:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        except Exception:
            providers = ['CPUExecutionProvider']
        app = FaceAnalysis(providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))
        
        img_np = np.array(pil_image.convert('RGB'))
        img_cv = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        
        faces = app.get(img_cv)
        
        if len(faces) > 0:
            face = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
            bbox = face.bbox.astype(int)
            
            h, w = img_cv.shape[:2]
            margin = 30
            x1 = max(0, bbox[0] - margin)
            y1 = max(0, bbox[1] - margin)
            x2 = min(w, bbox[2] + margin)
            y2 = min(h, bbox[3] + margin)
            
            cropped = img_cv[y1:y2, x1:x2]
            cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
            from PIL import Image
            logger.info(f"InsightFace detected face, returning cropped image")
            return Image.fromarray(cropped_rgb)
    except Exception as e:
        logger.error(f"InsightFace fallback failed: {e}", exc_info=True)
    
    return None

def analyze_image_pil(pil_image, model_id: str):
    model, processor = _get_model(model_id)

    # Try InsightFace first for face detection (optional)
    logger.info("Checking for faces with InsightFace first")
    cropped_face = _try_insightface_fallback(pil_image)

    # Use cropped face if available, otherwise use original image
    image_to_analyze = cropped_face if cropped_face is not None else pil_image

    # Resize to avoid OOM on Apple Silicon
    image_to_analyze = _resize_for_analysis(image_to_analyze)

    if cropped_face is None:
        logger.info("InsightFace not available or no face detected, using Qwen on original image")
    else:
        logger.info("Face detected by InsightFace, analyzing cropped face with Qwen")
    def _analyze_with_qwen(img):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": USER_PROMPT}
                ]
            }
        ]

        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt"
        )
        inputs = inputs.to(model.device)

        with torch.no_grad():
            generated_ids = model.generate(**inputs, max_new_tokens=150, do_sample=False)

        generated_ids_trimmed = [
            out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
        ]
        output_text = processor.batch_decode(
            generated_ids_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]

        logger.info(f"Qwen raw output: {output_text[:200]}")
        cleaned = qwen_json_guard.extract_json(output_text)
        try:
            return json.loads(cleaned)
        except Exception:
            return {"face_detected": False}

    # Analyze with Qwen
    logger.info("Running Qwen2-VL analysis...")
    data = _analyze_with_qwen(image_to_analyze)

    if not data.get("face_detected", False) and cropped_face is None:
        # Try one more time with InsightFace crop if we used original image
        logger.warning("Qwen didn't detect face on original image, trying InsightFace fallback...")
        cropped = _try_insightface_fallback(pil_image)
        if cropped:
            logger.info("InsightFace found face, re-analyzing cropped face with Qwen...")
            data = _analyze_with_qwen(cropped)
            data["face_detected"] = True
        else:
            logger.warning("InsightFace also not available or failed")

    if "face detected" in data:
        data["face_detected"] = bool(data.pop("face detected"))
    for k in ["hair_color","eyes_color","gender","hair_length","hair_style"]:
        if k not in data: data[k] = None
    if "full_description" not in data:
        data["full_description"] = None

    return data
