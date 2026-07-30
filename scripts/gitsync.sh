#!/usr/bin/env bash
# gitsync — commit / push / pull / PR / merge for a remote-less repo, via gh.
#
# These repos intentionally carry NO `origin` remote and NO GitHub URL in any
# tracked file. The target repo is supplied by the environment, never by git
# config:
#   1. $CSBENCH_REPO            e.g.  clousight/clousight-bench
#   2. a gitignored .gitsync.env at the repo root, e.g.
#          CSBENCH_REPO=clousight/clousight-bench
#
# Auth uses this machine's `gh` (which is wired as git's credential helper for
# github.com). Nothing here stores a token.
#
# Usage:
#   scripts/gitsync.sh commit "message"          # git add -A && git commit
#   scripts/gitsync.sh push [git-push args]       # push current branch
#   scripts/gitsync.sh pull [git-pull args]       # ff-only pull current branch
#   scripts/gitsync.sh pr "title" "body" [flags]  # gh pr create (head=current, base=main)
#   scripts/gitsync.sh merge <num> [flags]        # gh pr merge
#   scripts/gitsync.sh checks <num> [flags]       # gh pr checks
#   scripts/gitsync.sh status [flags]             # gh pr status
#   scripts/gitsync.sh repo                       # print the resolved slug
set -euo pipefail

die() { echo "gitsync: $*" >&2; exit 2; }

command -v gh >/dev/null || die "gh CLI not found on PATH"
toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repo"
cd "$toplevel"

# Resolve the target slug from the environment ONLY (never from git config).
if [[ -z "${CSBENCH_REPO:-}" && -f "$toplevel/.gitsync.env" ]]; then
  # shellcheck disable=SC1091
  source "$toplevel/.gitsync.env"
fi
SLUG="${CSBENCH_REPO:-}"
[[ -n "$SLUG" ]] || die "set CSBENCH_REPO=owner/repo, or create $toplevel/.gitsync.env"
URL="https://github.com/${SLUG}.git"
REMOTE="_gitsync"

# Attach an ephemeral remote for one git operation, then always detach it, so
# .git/config never persists a remote.
_ephemeral_remote() {
  git remote remove "$REMOTE" 2>/dev/null || true
  git remote add "$REMOTE" "$URL"
  trap 'git remote remove "'"$REMOTE"'" 2>/dev/null || true' EXIT
}

branch() { git branch --show-current; }

cmd="${1:-}"
[[ -n "$cmd" ]] && shift || true
case "$cmd" in
  commit)
    git add -A
    git commit "$@"
    ;;
  push)
    _ephemeral_remote
    git push "$REMOTE" "HEAD:$(branch)" "$@"
    ;;
  pull)
    _ephemeral_remote
    git pull --ff-only "$REMOTE" "$(branch)" "$@"
    ;;
  pr)
    [[ $# -ge 2 ]] || die 'usage: gitsync pr "<title>" "<body>" [gh flags]'
    title="$1"; body="$2"; shift 2
    _ephemeral_remote
    gh pr create -R "$SLUG" --head "$(branch)" --base main \
      --title "$title" --body "$body" "$@"
    ;;
  merge)
    gh pr merge -R "$SLUG" "$@"
    ;;
  checks)
    gh pr checks -R "$SLUG" "$@"
    ;;
  status)
    gh pr status -R "$SLUG" "$@"
    ;;
  repo)
    echo "$SLUG"
    ;;
  *)
    die "usage: gitsync {commit|push|pull|pr|merge|checks|status|repo} ..."
    ;;
esac
