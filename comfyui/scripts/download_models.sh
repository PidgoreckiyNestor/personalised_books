#!/bin/bash
# Model Download Script for RunPod Network Volume
#
# Run this script ONCE to download all required models to the Network Volume.
# After running, the models will persist and be available for all future runs.
#
# Usage:
#   1. Start a GPU pod with Network Volume attached
#   2. Run: bash /scripts/download_models.sh
#   3. Wait for all downloads to complete (~15GB total)
#   4. Models will persist on the Network Volume

set -e

MODELS_PATH="${MODELS_PATH:-/workspace/models}"

echo "=== ComfyUI Model Download Script ==="
echo "Downloading models to: $MODELS_PATH"
echo "Total download size: ~15GB"
echo ""

# Create all directories
mkdir -p "$MODELS_PATH/checkpoints"
mkdir -p "$MODELS_PATH/clip"
mkdir -p "$MODELS_PATH/clip_vision"
mkdir -p "$MODELS_PATH/controlnet"
mkdir -p "$MODELS_PATH/embeddings"
mkdir -p "$MODELS_PATH/loras"
mkdir -p "$MODELS_PATH/upscale_models"
mkdir -p "$MODELS_PATH/vae"
mkdir -p "$MODELS_PATH/ipadapter"
mkdir -p "$MODELS_PATH/insightface/models/buffalo_l"
mkdir -p "$MODELS_PATH/annotators"

download_file() {
    local url="$1"
    local dest="$2"
    local name="$3"

    if [ -f "$dest" ]; then
        echo "[SKIP] $name - already exists"
        return 0
    fi

    echo "[DOWNLOADING] $name..."
    wget -q --show-progress -O "$dest" "$url" || {
        echo "[ERROR] Failed to download $name"
        rm -f "$dest"
        return 1
    }
    echo "[OK] $name downloaded"
}

download_hf_file() {
    local repo="$1"
    local file="$2"
    local dest="$3"
    local name="$4"

    local url="https://huggingface.co/$repo/resolve/main/$file"
    download_file "$url" "$dest" "$name"
}

echo ""
echo "=== 1. Checkpoint Model ==="
# DreamShaper 8 - SD 1.5 checkpoint optimized for illustrations
download_file \
    "https://civitai.com/api/download/models/128713" \
    "$MODELS_PATH/checkpoints/dreamshaper_8.safetensors" \
    "DreamShaper 8 checkpoint (~2GB)"

echo ""
echo "=== 2. ControlNet Model ==="
download_hf_file \
    "lllyasviel/ControlNet-v1-1" \
    "control_v11p_sd15_lineart.pth" \
    "$MODELS_PATH/controlnet/control_v11p_sd15_lineart.pth" \
    "ControlNet Lineart (~1.4GB)"

echo ""
echo "=== 3. CLIP Vision Model ==="
# CLIP-ViT-H-14 for IPAdapter FaceID
download_hf_file \
    "h94/IP-Adapter" \
    "models/image_encoder/model.safetensors" \
    "$MODELS_PATH/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" \
    "CLIP Vision ViT-H-14 (~3.9GB)"

echo ""
echo "=== 4. IPAdapter FaceID Models ==="
# IPAdapter FaceID Plus V2
download_hf_file \
    "h94/IP-Adapter-FaceID" \
    "ip-adapter-faceid-plusv2_sd15.bin" \
    "$MODELS_PATH/ipadapter/ip-adapter-faceid-plusv2_sd15.bin" \
    "IPAdapter FaceID Plus V2 (~1.3GB)"

# Download to loras folder (where IPAdapterUnifiedLoaderFaceID looks for it)
download_hf_file \
    "h94/IP-Adapter-FaceID" \
    "ip-adapter-faceid-plusv2_sd15_lora.safetensors" \
    "$MODELS_PATH/loras/ip-adapter-faceid-plusv2_sd15_lora.safetensors" \
    "IPAdapter FaceID LoRA (~150MB)"

echo ""
echo "=== 5. Upscaler Model ==="
download_file \
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth" \
    "$MODELS_PATH/upscale_models/RealESRGAN_x2.pth" \
    "RealESRGAN x2 (~64MB)"

echo ""
echo "=== 6. DWPose Model ==="
download_hf_file \
    "hr16/DWPose-TorchScript-BatchSize5" \
    "dw-ll_ucoco_384_bs5.torchscript.pt" \
    "$MODELS_PATH/annotators/dw-ll_ucoco_384_bs5.torchscript.pt" \
    "DWPose TorchScript (~138MB)"

echo ""
echo "=== 7. InsightFace Models ==="
# buffalo_l model files
BUFFALO_URL="https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/buffalo_l"
BUFFALO_PATH="$MODELS_PATH/insightface/models/buffalo_l"

download_file \
    "$BUFFALO_URL/1k3d68.onnx" \
    "$BUFFALO_PATH/1k3d68.onnx" \
    "InsightFace 1k3d68"

download_file \
    "$BUFFALO_URL/2d106det.onnx" \
    "$BUFFALO_PATH/2d106det.onnx" \
    "InsightFace 2d106det"

download_file \
    "$BUFFALO_URL/det_10g.onnx" \
    "$BUFFALO_PATH/det_10g.onnx" \
    "InsightFace det_10g"

download_file \
    "$BUFFALO_URL/genderage.onnx" \
    "$BUFFALO_PATH/genderage.onnx" \
    "InsightFace genderage"

download_file \
    "$BUFFALO_URL/w600k_r50.onnx" \
    "$BUFFALO_PATH/w600k_r50.onnx" \
    "InsightFace w600k_r50"

# inswapper model
download_file \
    "https://huggingface.co/MonsterMMORPG/tools/resolve/main/inswapper_128.onnx" \
    "$MODELS_PATH/insightface/models/inswapper_128.onnx" \
    "inswapper_128 (~500MB)"

echo ""
echo "=== 8. Optional: PiDiNet Model ==="
# PiDiNet will auto-download, but we can pre-download it
download_file \
    "https://huggingface.co/lllyasviel/Annotators/resolve/main/table5_pidinet.pth" \
    "$MODELS_PATH/annotators/table5_pidinet.pth" \
    "PiDiNet table5 (~3MB)"

echo ""
echo "=== Download Complete ==="
echo ""
echo "All models downloaded to: $MODELS_PATH"
echo ""
echo "Directory structure:"
find "$MODELS_PATH" -type f -exec ls -lh {} \; | awk '{print $9, $5}'
echo ""
echo "Total size:"
du -sh "$MODELS_PATH"
echo ""
echo "You can now start ComfyUI. Models will be loaded from the Network Volume."
