#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTEXT_DIR="$(dirname "$SCRIPT_DIR")"

TAG_VERSION="${TAG_VERSION:-v4.7}"

IMAGES=(
  "vibe-fdtr:full-${TAG_VERSION}"
  "vibe-fdtr:code-${TAG_VERSION}"
  "vibe-fdtr:minimal-${TAG_VERSION}"
)

echo "========================================"
echo "  FDTR Docker Image Builder"
echo "  Version: ${TAG_VERSION}"
echo "  Context: ${CONTEXT_DIR}"
echo "========================================"
echo ""

echo "[1/3] Building full image: vibe-fdtr:full-${TAG_VERSION}"
docker build -f "${SCRIPT_DIR}/full.Dockerfile" -t "vibe-fdtr:full-${TAG_VERSION}" "${CONTEXT_DIR}"
echo ""

echo "[2/3] Building code image: vibe-fdtr:code-${TAG_VERSION}"
docker build --build-arg BASE_TAG="vibe-fdtr:full-${TAG_VERSION}" -f "${SCRIPT_DIR}/code.Dockerfile" -t "vibe-fdtr:code-${TAG_VERSION}" "${CONTEXT_DIR}"
echo ""

echo "[3/3] Building minimal image: vibe-fdtr:minimal-${TAG_VERSION}"
docker build --build-arg BASE_TAG="vibe-fdtr:full-${TAG_VERSION}" -f "${SCRIPT_DIR}/minimal.Dockerfile" -t "vibe-fdtr:minimal-${TAG_VERSION}" "${CONTEXT_DIR}"
echo ""

echo "========================================"
echo "  Build complete. Verify with:"
echo "    docker images | grep vibe-fdtr"
echo "========================================"
