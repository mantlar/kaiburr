#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the kaiburr root directory (parent of docker/)
KAIBURR_DIR="$(dirname "$SCRIPT_DIR")"
# Parent directory (usually where mb2server resides)
PARENT_DIR="$(dirname "$KAIBURR_DIR")"

IMAGE_NAME="kaiburr"

echo "Building Docker image ($IMAGE_NAME)..."
docker build -f "$SCRIPT_DIR/Dockerfile" -t "$IMAGE_NAME" "$KAIBURR_DIR"

if [ $? -ne 0 ]; then
  echo "[ERROR] Docker build failed! Exiting."
  exit 1
fi

echo "[OK] Build complete. Starting container..."
# Auto-detect MBII server folder or fallback to parent
SERVER_DIR="$PARENT_DIR"
if [ -d "$PARENT_DIR/mb2server" ]; then
  SERVER_DIR="$PARENT_DIR/mb2server"
fi

docker run --rm -it \
    -p 29070:29070/udp \
    -p 29070:29070/tcp \
    -p 6767:6767/tcp \
    -v "$SERVER_DIR:/app/mb2server" \
    -v "$KAIBURR_DIR:/app/kaiburr" \
    -v kaiburr_venv:/app/kaiburr/venv \
    "$IMAGE_NAME" "$@"