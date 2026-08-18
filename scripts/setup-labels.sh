#!/usr/bin/env bash
# setup-labels.sh — create/update the project's issue+PR labels via the gh CLI.
#
# Idempotent: `gh label create --force` updates a label if it already exists.
# One-time (or occasional) maintainer task; not wired into CI to avoid giving a
# workflow write access to repository metadata.
#
# Usage:
#   gh auth status                 # confirm you are the clousight-dev account
#   ./scripts/setup-labels.sh      # applies to the repo the cwd is linked to
#   REPO=clousight/clousight-bench ./scripts/setup-labels.sh   # or target explicitly

set -euo pipefail

ALLOWED_GH_USER="clousight-dev"
login="$(gh api user --jq .login 2>/dev/null)" \
  || { echo "setup-labels: gh is not authenticated; run: gh auth login" >&2; exit 2; }
[[ "$login" == "$ALLOWED_GH_USER" ]] \
  || { echo "setup-labels: active gh account is '$login'; switch with: gh auth switch --user $ALLOWED_GH_USER" >&2; exit 2; }

REPO_FLAG=()
if [[ -n "${REPO:-}" ]]; then
  REPO_FLAG=(--repo "$REPO")
fi

label() {
  # label <name> <color-hex> <description>
  gh label create "$1" --color "$2" --description "$3" --force "${REPO_FLAG[@]}"
}

echo "Syncing labels..."

# --- type ---------------------------------------------------------------------
label "bug"          "d73a4a" "Something isn't working"
label "enhancement"  "a2eeef" "New feature or request"
label "docs"         "0075ca" "Documentation only"
label "refactor"     "cfd3d7" "Internal change, no user-visible behavior"
label "test"         "bfd4f2" "Tests / CI only"
label "question"     "d876e3" "Further information is requested"

# --- area (match the codebase seams) -----------------------------------------
label "area: core"     "5319e7" "Lifecycle orchestrator, schema, registry"
label "area: adapter"  "1d76db" "A ProviderAdapter / cloud transport"
label "area: domain"   "0e8a16" "A DomainPack (agent-runtime, bigdata-emr, …)"
label "area: report"   "fbca04" "Reporting / analytics / query"
label "area: cost"     "c5def5" "Cost attribution, pricing, budget"
label "area: ci"       "ededed" "CI / release / packaging"

# --- meta / triage ------------------------------------------------------------
label "good first issue" "7057ff" "Good for newcomers"
label "help wanted"      "008672" "Extra attention is wanted"
label "needs-repro"      "fef2c0" "Waiting on a reproduction"
label "blocked"          "b60205" "Blocked on something else"
label "breaking"         "b60205" "Backwards-incompatible change"
label "duplicate"        "cfd3d7" "Already tracked elsewhere"
label "wontfix"          "ffffff" "Out of scope / will not be worked on"

echo "Done."
