#!/usr/bin/env bash
# Build + push the custom cinder-backup-pbs image.
# Usage:
#   ./ci/build-image.sh [tag]
# Env:
#   REGISTRY  (default: registry.eu-west-1.cloud.pawhost.de)
#   CINDER_BASE_IMAGE  (default: quay.io/airshipit/cinder:2026.1-ubuntu_noble)
set -euo pipefail

REGISTRY="${REGISTRY:-registry.eu-west-1.cloud.pawhost.de}"
CINDER_BASE_IMAGE="${CINDER_BASE_IMAGE:-quay.io/airshipit/cinder:2026.1-ubuntu_noble}"
TAG="${1:-$(git rev-parse --short HEAD)}"
IMAGE="${REGISTRY}/cinder-backup-pbs:${TAG}"

cd "$(dirname "$0")/.."

docker build \
  --build-arg CINDER_IMAGE="${CINDER_BASE_IMAGE}" \
  -t "${IMAGE}" \
  -f ci/Dockerfile \
  .

docker push "${IMAGE}"
echo "pushed: ${IMAGE}"
