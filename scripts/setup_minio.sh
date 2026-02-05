#!/bin/bash
# MinIO Bucket Setup Script
#
# This script creates the required bucket and uploads templates to MinIO.
# Run this after starting docker-compose.local.yml.
#
# Prerequisites:
#   - MinIO Client (mc) installed: brew install minio/stable/mc
#   - docker-compose.local.yml services running
#
# Usage:
#   bash scripts/setup_minio.sh

set -e

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://localhost:9000}"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-minioadmin}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-minioadmin}"
BUCKET_NAME="${BUCKET_NAME:-personalized-books}"

echo "=== MinIO Setup Script ==="
echo "Endpoint: $MINIO_ENDPOINT"
echo "Bucket: $BUCKET_NAME"
echo ""

# Check if mc is installed
if ! command -v mc &> /dev/null; then
    echo "ERROR: MinIO Client (mc) is not installed."
    echo ""
    echo "Install with:"
    echo "  macOS:  brew install minio/stable/mc"
    echo "  Linux:  wget https://dl.min.io/client/mc/release/linux-amd64/mc && chmod +x mc && sudo mv mc /usr/local/bin/"
    echo ""
    exit 1
fi

# Configure mc alias
echo "Configuring MinIO alias..."
mc alias set local "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" --api S3v4

# Create bucket
echo "Creating bucket '$BUCKET_NAME'..."
mc mb "local/$BUCKET_NAME" --ignore-existing

# Set bucket policy to allow public read (for development)
echo "Setting bucket policy..."
mc anonymous set download "local/$BUCKET_NAME"

# Upload templates if they exist
TEMPLATES_DIR="backend/templates"
if [ -d "$TEMPLATES_DIR" ]; then
    echo ""
    echo "Uploading templates..."

    for template in "$TEMPLATES_DIR"/*; do
        if [ -d "$template" ]; then
            template_name=$(basename "$template")
            echo "  Uploading template: $template_name"
            mc cp --recursive "$template/" "local/$BUCKET_NAME/templates/$template_name/"
        fi
    done

    echo "Templates uploaded successfully"
else
    echo "WARNING: Templates directory not found at $TEMPLATES_DIR"
    echo "Skipping template upload"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "MinIO Console: http://localhost:9001"
echo "  Username: $MINIO_ACCESS_KEY"
echo "  Password: $MINIO_SECRET_KEY"
echo ""
echo "S3 Configuration for backend/.env.local:"
echo "  AWS_ENDPOINT_URL=$MINIO_ENDPOINT"
echo "  AWS_ACCESS_KEY_ID=$MINIO_ACCESS_KEY"
echo "  AWS_SECRET_ACCESS_KEY=$MINIO_SECRET_KEY"
echo "  S3_BUCKET_NAME=$BUCKET_NAME"
echo ""
