#!/usr/bin/env bash
# build-push.sh — build the cb-probe image and push it to Aliyun ACR.
#
# Required env vars:
#   CB_ACR_NAMESPACE   ACR namespace (e.g. "clousight")
#   CB_ACR_USER        ACR username (from terraform output or ACR console)
#   CB_ACR_PASSWORD    ACR password / access token (never hardcoded)
#
# Optional env vars:
#   CB_ACR_REGION      Aliyun region (default: cn-hangzhou)
#
# Usage:
#   CB_ACR_NAMESPACE=clousight CB_ACR_USER=... CB_ACR_PASSWORD=... ./deploy/cb-probe/build-push.sh
#
# The script prints the registry-vpc image reference at the end — that value
# goes into the terraform `eci_image` variable (or csbench target `eci_image`).

set -euo pipefail

# --- Configuration ---
CB_ACR_REGION="${CB_ACR_REGION:-cn-hangzhou}"

: "${CB_ACR_NAMESPACE:?CB_ACR_NAMESPACE must be set (ACR namespace)}"
: "${CB_ACR_USER:?CB_ACR_USER must be set (ACR username)}"
: "${CB_ACR_PASSWORD:?CB_ACR_PASSWORD must be set (ACR password)}"

REPO="cb-probe"
PUBLIC_REGISTRY="registry.${CB_ACR_REGION}.aliyuncs.com"
VPC_REGISTRY="registry-vpc.${CB_ACR_REGION}.aliyuncs.com"

# Derive a deterministic tag from the current git commit SHA (short).
GIT_TAG="$(git rev-parse --short HEAD)"

PUBLIC_IMAGE="${PUBLIC_REGISTRY}/${CB_ACR_NAMESPACE}/${REPO}:${GIT_TAG}"
VPC_IMAGE="${VPC_REGISTRY}/${CB_ACR_NAMESPACE}/${REPO}:${GIT_TAG}"

# Resolve the repo root regardless of where the script is invoked from.
REPO_ROOT="$(git rev-parse --show-toplevel)"

echo "[cb-probe] Building image: ${PUBLIC_IMAGE}"
echo "[cb-probe] Build context: ${REPO_ROOT}"

docker build \
  --file "${REPO_ROOT}/deploy/cb-probe/Dockerfile" \
  --tag "${PUBLIC_IMAGE}" \
  "${REPO_ROOT}"

echo "[cb-probe] Logging in to ${PUBLIC_REGISTRY}"
echo "${CB_ACR_PASSWORD}" | docker login \
  --username "${CB_ACR_USER}" \
  --password-stdin \
  "${PUBLIC_REGISTRY}"

echo "[cb-probe] Pushing ${PUBLIC_IMAGE}"
docker push "${PUBLIC_IMAGE}"

echo
echo "[cb-probe] Build and push complete."
echo "[cb-probe] ECI image reference (registry-vpc, for eci_image var):"
echo "  ${VPC_IMAGE}"
