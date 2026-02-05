#!/bin/bash
set -e

# Configuration
COMFYUI_DIR="${COMFYUI_DIR:-/workspace/ComfyUI}"
VENV_DIR="$COMFYUI_DIR/.venv"
MODELS_DIR="/workspace/models"

echo "=== ComfyUI Face Swap - RunPod Startup ==="
echo "Started at $(date)"

# ---------------------------------------------------------------------------- #
#                          Custom Nodes Configuration                           #
# ---------------------------------------------------------------------------- #

# Custom nodes required for face swap workflow
CUSTOM_NODES=(
    "https://github.com/ltdrdata/ComfyUI-Manager.git"
    "https://github.com/cubiq/ComfyUI_IPAdapter_plus.git"
    "https://github.com/ltdrdata/ComfyUI-Impact-Pack.git"
    "https://github.com/Kosinkadink/ComfyUI-Advanced-ControlNet.git"
    "https://github.com/Fannovel16/comfyui_controlnet_aux.git"
    "https://github.com/WASasquatch/was-node-suite-comfyui.git"
    "https://github.com/cubiq/ComfyUI_essentials.git"
    "https://github.com/kijai/ComfyUI-KJNodes.git"
)

# ---------------------------------------------------------------------------- #
#                          Setup Functions                                       #
# ---------------------------------------------------------------------------- #

setup_comfyui() {
    echo "Setting up ComfyUI..."

    # Clone ComfyUI if not present
    if [ ! -d "$COMFYUI_DIR" ]; then
        echo "Cloning ComfyUI..."
        cd /workspace
        git clone https://github.com/comfyanonymous/ComfyUI.git
    fi

    # Create model directories
    mkdir -p "$COMFYUI_DIR/models/checkpoints"
    mkdir -p "$COMFYUI_DIR/models/clip"
    mkdir -p "$COMFYUI_DIR/models/clip_vision"
    mkdir -p "$COMFYUI_DIR/models/controlnet"
    mkdir -p "$COMFYUI_DIR/models/ipadapter"
    mkdir -p "$COMFYUI_DIR/models/upscale_models"
    mkdir -p "$COMFYUI_DIR/models/loras"
    mkdir -p "$COMFYUI_DIR/models/vae"
    mkdir -p "$COMFYUI_DIR/models/embeddings"

    # Create input/output directories
    mkdir -p "$COMFYUI_DIR/input"
    mkdir -p "$COMFYUI_DIR/output"
}

setup_custom_nodes() {
    echo "Setting up custom nodes..."
    mkdir -p "$COMFYUI_DIR/custom_nodes"

    for repo in "${CUSTOM_NODES[@]}"; do
        repo_name=$(basename "$repo" .git)
        if [ ! -d "$COMFYUI_DIR/custom_nodes/$repo_name" ]; then
            echo "Installing $repo_name..."
            cd "$COMFYUI_DIR/custom_nodes"
            git clone --depth 1 "$repo" || true
        fi
    done
}

setup_venv() {
    echo "Setting up Python virtual environment..."

    if [ ! -d "$VENV_DIR" ]; then
        echo "Creating venv with system-site-packages..."
        cd "$COMFYUI_DIR"
        python3.12 -m venv --system-site-packages "$VENV_DIR"
        source "$VENV_DIR/bin/activate"

        # Ensure pip is available
        python -m ensurepip --upgrade
        python -m pip install --upgrade pip

        # Install ComfyUI requirements
        echo "Installing ComfyUI requirements..."
        pip install -r "$COMFYUI_DIR/requirements.txt"

        # Install custom node dependencies
        echo "Installing custom node dependencies..."
        cd "$COMFYUI_DIR/custom_nodes"
        for node_dir in */; do
            if [ -d "$node_dir" ]; then
                cd "$COMFYUI_DIR/custom_nodes/$node_dir"

                if [ -f "requirements.txt" ]; then
                    echo "Installing requirements for $node_dir"
                    pip install --no-cache-dir -r requirements.txt || true
                fi

                if [ -f "install.py" ]; then
                    echo "Running install.py for $node_dir"
                    python install.py || true
                fi
            fi
        done
    else
        echo "Using existing venv..."
        source "$VENV_DIR/bin/activate"
    fi
}

setup_models() {
    echo "Setting up model paths..."

    # Create models directory on workspace (Network Volume)
    mkdir -p "$MODELS_DIR/checkpoints"
    mkdir -p "$MODELS_DIR/clip_vision"
    mkdir -p "$MODELS_DIR/controlnet"
    mkdir -p "$MODELS_DIR/ipadapter"
    mkdir -p "$MODELS_DIR/upscale_models"
    mkdir -p "$MODELS_DIR/insightface/models/buffalo_l"
    mkdir -p "$MODELS_DIR/annotators"

    # Set InsightFace home
    export INSIGHTFACE_HOME="$MODELS_DIR/insightface"
    echo "INSIGHTFACE_HOME=$INSIGHTFACE_HOME"

    # Copy extra_model_paths.yaml if not present
    if [ ! -f "$COMFYUI_DIR/extra_model_paths.yaml" ]; then
        cp /extra_model_paths.yaml "$COMFYUI_DIR/extra_model_paths.yaml"
    fi

    # Setup custom node model caches (DWPose, PiDiNet)
    setup_controlnet_aux_models
}

setup_controlnet_aux_models() {
    echo "Setting up controlnet_aux model caches..."

    CONTROLNET_AUX_DIR="$COMFYUI_DIR/custom_nodes/comfyui_controlnet_aux"

    if [ -d "$CONTROLNET_AUX_DIR" ]; then
        # DWPose
        DWPOSE_SRC="$MODELS_DIR/annotators/dw-ll_ucoco_384_bs5.torchscript.pt"
        DWPOSE_DST_DIR="$CONTROLNET_AUX_DIR/ckpts/hr16/DWPose-TorchScript-BatchSize5"
        DWPOSE_DST="$DWPOSE_DST_DIR/dw-ll_ucoco_384_bs5.torchscript.pt"

        if [ -f "$DWPOSE_SRC" ] && [ ! -f "$DWPOSE_DST" ]; then
            echo "Copying DWPose model..."
            mkdir -p "$DWPOSE_DST_DIR"
            cp "$DWPOSE_SRC" "$DWPOSE_DST"
        fi

        # PiDiNet
        PIDINET_SRC="$MODELS_DIR/annotators/table5_pidinet.pth"
        PIDINET_DST_DIR="$CONTROLNET_AUX_DIR/ckpts/lllyasviel/Annotators"
        PIDINET_DST="$PIDINET_DST_DIR/table5_pidinet.pth"

        if [ -f "$PIDINET_SRC" ] && [ ! -f "$PIDINET_DST" ]; then
            echo "Copying PiDiNet model..."
            mkdir -p "$PIDINET_DST_DIR"
            cp "$PIDINET_SRC" "$PIDINET_DST"
        fi
    fi
}

check_models() {
    echo ""
    echo "=== Model Status ==="

    check_model() {
        local path="$1"
        local name="$2"
        if [ -f "$path" ]; then
            echo "[OK] $name"
        else
            echo "[MISSING] $name"
        fi
    }

    check_model "$MODELS_DIR/checkpoints/dreamshaper_8.safetensors" "Checkpoint: dreamshaper_8"
    check_model "$MODELS_DIR/controlnet/control_v11p_sd15_lineart.pth" "ControlNet: lineart"
    check_model "$MODELS_DIR/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors" "CLIP Vision"
    check_model "$MODELS_DIR/ipadapter/ip-adapter-faceid-plusv2_sd15.bin" "IPAdapter FaceID Plus V2"
    check_model "$MODELS_DIR/ipadapter/ip-adapter-faceid-plusv2_sd15_lora.safetensors" "IPAdapter FaceID LoRA"
    check_model "$MODELS_DIR/upscale_models/RealESRGAN_x2.pth" "Upscaler: RealESRGAN_x2"
    check_model "$MODELS_DIR/insightface/models/buffalo_l/det_10g.onnx" "InsightFace buffalo_l"

    echo ""
}

# ---------------------------------------------------------------------------- #
#                               Main Program                                     #
# ---------------------------------------------------------------------------- #

# Run setup
setup_comfyui
setup_custom_nodes
setup_venv
setup_models
check_models

# Start ComfyUI
echo "=== Starting ComfyUI ==="
cd "$COMFYUI_DIR"

# Use extra_model_paths.yaml if models are on Network Volume
EXTRA_ARGS=""
if [ -f "$COMFYUI_DIR/extra_model_paths.yaml" ]; then
    EXTRA_ARGS="--extra-model-paths-config $COMFYUI_DIR/extra_model_paths.yaml"
fi

exec python main.py \
    --listen 0.0.0.0 \
    --port 8188 \
    $EXTRA_ARGS \
    "$@"
