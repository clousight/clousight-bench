#!/usr/bin/env bash
# Build the viewer frontend: web/ sources -> src/clousight_bench/resources/viewer/dist/
# (the built dist/ is committed and shipped in the wheel).
set -euo pipefail
cd "$(dirname "$0")/../web"
npm ci
npm run build
