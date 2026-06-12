#!/bin/bash

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the kaiburr root directory (parent of docker/)
KAIBURR_DIR="$(dirname "$SCRIPT_DIR")"
# Get the parent of kaiburr (where dockerize should be)
PARENT_DIR="$(dirname "$KAIBURR_DIR")"

IMAGE_NAME="kaiburr"

echo "Building Docker image..."
docker build -f "$SCRIPT_DIR/Dockerfile" -t $IMAGE_NAME "$KAIBURR_DIR"

if [ $? -ne 0 ]; then
  echo "Docker build failed! Exiting."
  exit 1
fi

# Ensure Jedi Academy & Moviebattles II is installed in $PARENT_DIR/dockerize

echo "Running Docker container..."
docker run --rm -it \
    -v "$PARENT_DIR/dockerize:/app/jediacademy" \
    -v "$KAIBURR_DIR:/app/jediacademy/gamedata/kaiburr" \
    -v "$PARENT_DIR/configstore_kaiburr:/app/jediacademy/gamedata/kaiburr/data" \
    $IMAGE_NAME "$@"