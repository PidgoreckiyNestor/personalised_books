#!/bin/bash
set -e

echo "=== ComfyUI RunPod Entrypoint ==="
echo "Starting at $(date)"

# Configuration
VOLUME_PATH="${RUNPOD_VOLUME_PATH:-/runpod-volume}"
COMFYUI_PATH="${COMFYUI_PATH:-/home/runner/ComfyUI}"
MODELS_PATH="$VOLUME_PATH/models"

# Check if Network Volume is mounted
if [ -d "$VOLUME_PATH" ]; then
    echo "Network Volume detected at $VOLUME_PATH"

    # Create model directories if they don't exist
    mkdir -p "$MODELS_PATH/checkpoints"
    mkdir -p "$MODELS_PATH/clip"
    mkdir -p "$MODELS_PATH/clip_vision"
    mkdir -p "$MODELS_PATH/controlnet"
    mkdir -p "$MODELS_PATH/embeddings"
    mkdir -p "$MODELS_PATH/loras"
    mkdir -p "$MODELS_PATH/upscale_models"
    mkdir -p "$MODELS_PATH/vae"
    mkdir -p "$MODELS_PATH/ipadapter"
    mkdir -p "$MODELS_PATH/insightface"
    mkdir -p "$MODELS_PATH/annotators"
    mkdir -p "$VOLUME_PATH/input"
    mkdir -p "$VOLUME_PATH/output"

    # Set InsightFace home to Network Volume
    export INSIGHTFACE_HOME="$MODELS_PATH/insightface"
    echo "INSIGHTFACE_HOME set to $INSIGHTFACE_HOME"

    # Set HuggingFace cache to Network Volume
    export HF_HOME="$VOLUME_PATH/huggingface"
    mkdir -p "$HF_HOME"
    echo "HF_HOME set to $HF_HOME"
else
    echo "WARNING: Network Volume not mounted at $VOLUME_PATH"
    echo "Models must be present in $COMFYUI_PATH/models/"
fi

# === Copy models to custom node locations ===
# These custom nodes expect models in specific hardcoded locations

# DWPose model for comfyui_controlnet_aux
DWPOSE_SRC="$MODELS_PATH/annotators/dw-ll_ucoco_384_bs5.torchscript.pt"
DWPOSE_DST_DIR="$COMFYUI_PATH/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/DWPose-TorchScript-BatchSize5"
DWPOSE_DST="$DWPOSE_DST_DIR/dw-ll_ucoco_384_bs5.torchscript.pt"

if [ -f "$DWPOSE_SRC" ] && [ ! -f "$DWPOSE_DST" ]; then
    echo "Copying DWPose model to custom_nodes location..."
    mkdir -p "$DWPOSE_DST_DIR"
    cp "$DWPOSE_SRC" "$DWPOSE_DST"
    echo "DWPose model copied successfully"
elif [ -f "$DWPOSE_DST" ]; then
    echo "DWPose model already present"
else
    echo "WARNING: DWPose model not found at $DWPOSE_SRC"
    echo "DWPose will attempt to download it on first use (may cause delays)"
fi

# PiDiNet model for comfyui_controlnet_aux
PIDINET_SRC="$MODELS_PATH/annotators/table5_pidinet.pth"
PIDINET_DST_DIR="$COMFYUI_PATH/custom_nodes/comfyui_controlnet_aux/ckpts/lllyasviel/Annotators"
PIDINET_DST="$PIDINET_DST_DIR/table5_pidinet.pth"

if [ -f "$PIDINET_SRC" ] && [ ! -f "$PIDINET_DST" ]; then
    echo "Copying PiDiNet model to custom_nodes location..."
    mkdir -p "$PIDINET_DST_DIR"
    cp "$PIDINET_SRC" "$PIDINET_DST"
    echo "PiDiNet model copied successfully"
elif [ -f "$PIDINET_DST" ]; then
    echo "PiDiNet model already present"
else
    echo "WARNING: PiDiNet model not found at $PIDINET_SRC"
    echo "PiDiNet will attempt to download it on first use"
fi

# YOLO-NAS models (optional bbox detector for DWPose)
YOLONAS_SRC_DIR="$MODELS_PATH/annotators"
YOLONAS_DST_DIR="$COMFYUI_PATH/custom_nodes/comfyui_controlnet_aux/ckpts/hr16/yolo-nas-fp16"

for model in yolo_nas_s_fp16.onnx yolo_nas_m_fp16.onnx yolo_nas_l_fp16.onnx; do
    src="$YOLONAS_SRC_DIR/$model"
    dst="$YOLONAS_DST_DIR/$model"

    # Remove zero-byte placeholders
    if [ -f "$dst" ] && [ ! -s "$dst" ]; then
        echo "Removing zero-byte placeholder: $dst"
        rm -f "$dst"
    fi

    if [ -f "$src" ] && [ -s "$src" ] && [ ! -f "$dst" ]; then
        echo "Copying $model to custom_nodes location..."
        mkdir -p "$YOLONAS_DST_DIR"
        cp "$src" "$dst"
    fi
done

# === InsightFace buffalo_l model check ===
BUFFALO_PATH="$INSIGHTFACE_HOME/models/buffalo_l"
if [ -d "$BUFFALO_PATH" ]; then
    echo "InsightFace buffalo_l model found at $BUFFALO_PATH"
else
    echo "WARNING: InsightFace buffalo_l model not found"
    echo "IPAdapterInsightFaceLoader may download it on first use"
fi

# === inswapper model check ===
INSWAPPER_PATH="$INSIGHTFACE_HOME/models/inswapper_128.onnx"
if [ -f "$INSWAPPER_PATH" ]; then
    echo "inswapper_128.onnx found"
    export INSIGHTFACE_MODEL_PATH="$INSWAPPER_PATH"
else
    echo "WARNING: inswapper_128.onnx not found at $INSWAPPER_PATH"
fi

# === Verify required models ===
echo ""
echo "=== Model Status Check ==="

check_model() {
    local path="$1"
    local name="$2"
    if [ -f "$path" ]; then
        echo "[OK] $name"
    else
        echo "[MISSING] $name - $path"
    fi
}

check_model "$MODELS_PATH/checkpoints/dreamshaper_8.safetensors" "Checkpoint: dreamshaper_8"
check_model "$MODELS_PATH/controlnet/control_v11p_sd15_lineart.pth" "ControlNet: lineart"
check_model "$MODELS_PATH/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" "CLIP Vision"
check_model "$MODELS_PATH/upscale_models/RealESRGAN_x2.pth" "Upscaler: RealESRGAN_x2"
check_model "$MODELS_PATH/ipadapter/ip-adapter-faceid-plusv2_sd15.bin" "IPAdapter FaceID Plus V2"
check_model "$MODELS_PATH/ipadapter/ip-adapter-faceid-plusv2_sd15_lora.safetensors" "IPAdapter FaceID LoRA"

echo ""
echo "=== Starting ComfyUI ==="

# Set offline mode for ComfyUI-Manager to avoid startup delays
if command -v comfy &> /dev/null; then
    echo "Setting ComfyUI-Manager to offline mode..."
    comfy manager set-mode offline 2>/dev/null || true
fi

# Launch ComfyUI with GPU support
cd "$COMFYUI_PATH"

exec python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    --extra-model-paths-config /home/runner/ComfyUI/extra_model_paths.yaml \
    "$@"
