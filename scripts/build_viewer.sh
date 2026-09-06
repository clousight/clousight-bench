#!/usr/bin/env bash
# Build the viewer frontend: web/ sources -> src/clousight_bench/resources/viewer/dist/
# (the built dist/ is committed and shipped in the wheel).
set -euo pipefail
cd "$(dirname "$0")/../web"
npm ci
# The unit tests cover the viewer's pure logic (glossary, formatters, ETA, the
# progress-stream reducer, the router). Running them here means a local build
# fails for the same reason CI would, rather than after the push.
npm test
npm run build
