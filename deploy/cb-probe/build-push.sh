#!/usr/bin/env bash
# build-push.sh — build the OPTIONAL cb-probe image and push it to any registry.
#
# You normally do NOT need this: by default the probe installs itself from the
# public repo at container boot (no build, no registry). Build a pre-baked image
# only to shave the boot-time pip install off ECI/Fargate cold starts.
#
# Registry-agnostic — works for Aliyun ACR, AWS ECR, GHCR, Docker Hub, etc.
#
# Required env vars:
#   CB_REGISTRY           Registry host, e.g. registry.cn-hangzhou.aliyuncs.com | ghcr.io
#   CB_IMAGE_REPO         Repo path, e.g. <namespace>/cb-probe
#   CB_REGISTRY_USER      Push username
#   CB_REGISTRY_PASSWORD  Push password / token (never hardcoded)
#
# Usage:
#   CB_REGISTRY=ghcr.io CB_IMAGE_REPO=clousight/cb-probe \
#   CB_REGISTRY_USER=... CB_REGISTRY_PASSWORD=... ./deploy/cb-probe/build-push.sh
#
# Prints the pull reference at the end — set it as csbench target `eci_image`
# (or terraform var.eci_image).

set -euo pipefail

: "${CB_REGISTRY:?CB_REGISTRY must be set (e.g. ghcr.io or registry.cn-hangzhou.aliyuncs.com)}"
: "${CB_IMAGE_REPO:?CB_IMAGE_REPO must be set (e.g. <namespace>/cb-probe)}"
: "${CB_REGISTRY_USER:?CB_REGISTRY_USER must be set}"
: "${CB_REGISTRY_PASSWORD:?CB_REGISTRY_PASSWORD must be set}"

# Deterministic tag from the current git commit SHA — matches the code_ref a
# bootstrap run would install, so the image and the from-source path stay in sync.
GIT_TAG="$(git rev-parse --short HEAD)"
IMAGE="${CB_REGISTRY}/${CB_IMAGE_REPO}:${GIT_TAG}"

REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "[cb-probe] Building image: ${IMAGE}"
echo "[cb-probe] Build context: ${REPO_ROOT}"
docker build \
  --file "${REPO_ROOT}/deploy/cb-probe/Dockerfile" \
  --tag "${IMAGE}" \
  "${REPO_ROOT}"

echo "[cb-probe] Logging in to ${CB_REGISTRY}"
echo "${CB_REGISTRY_PASSWORD}" | docker login \
  --username "${CB_REGISTRY_USER}" \
  --password-stdin \
  "${CB_REGISTRY}"

echo "[cb-probe] Pushing ${IMAGE}"
docker push "${IMAGE}"

echo
echo "[cb-probe] Build and push complete."
echo "[cb-probe] Set this as csbench target eci_image (or terraform var.eci_image):"
echo "  ${IMAGE}"
echo "[cb-probe] For a private Aliyun-internal pull, mirror to ACR and use the"
echo "           registry-vpc.<region>.aliyuncs.com/... form instead."
