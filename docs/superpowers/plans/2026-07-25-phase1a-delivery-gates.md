# Phase 1A Delivery Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the finished Phase 1A work on both repositories' `main` branches through reviewed pull requests, give Pro a minimal cross-repository CI, protect both `main` branches with rulesets, and make the public documentation tell the truth about the now-public Core repository.

**Architecture:** No product code changes. This plan only moves already-written commits through GitHub delivery gates in a fixed order: Core PR → Core merge → Pro CI → Pro PR → Pro merge → rulesets on both `main` branches → documentation correction merged *through* the new ruleset (which doubles as the ruleset's end-to-end verification).

**Tech Stack:** GitHub Actions, the GitHub `gh` CLI through the repository-pinned identity wrapper, the GitHub repository-rulesets REST API, `uv` for Python workspace resolution, `git` worktrees.

## Global Constraints

- **GitHub identity is `clousight-dev`.** Plain `gh` is authenticated as `legend91325` on this machine and must never be used for these two repositories. Every GitHub command in this plan uses `/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh`, which injects `GH_CONFIG_DIR=$HOME/.config/gh-clousight`; do not mutate the global active account.
- The exact GitHub command prefix is `/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh`. The shorter shell variable `GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh` is set in every command block that calls GitHub.
- Core repository: `clousight/clousight-bench` — **public**, Apache-2.0, default branch `main`.
- Pro repository: `clousight/clousight-bench-pro` — **private**, proprietary, default branch `main`. Never push Pro code, Pro package names or Pro file contents to the Core repository.
- Core commits use DCO sign-off: `git commit -s`. Pro commits use `git commit -s` as well for consistency.
- Core version stays `0.2.0` with classifier `Development Status :: 3 - Alpha`. This plan changes no version numbers.
- Core `main` required checks are exactly: `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `wheel-smoke`.
- Pro `main` required check is exactly: `core-compat`.
- Both `main` rulesets: pull request required, `required_approving_review_count = 0`, force push blocked, deletion blocked, no bypass actors (admins included).
- Current entitlement fact (verified 2026-07-25): Core ruleset listing returns `[]`; Pro ruleset and classic branch-protection APIs return HTTP `403` with `Upgrade to GitHub Pro or make this repository public to enable this feature.` Task 5 therefore has a mandatory GitHub organization-plan upgrade precondition; never make Pro public and never pretend protection succeeded.
- No new product capability, no real cloud adapter, no commercial service, no ResultRecord or Task API change. Those belong to `docs/superpowers/plans/2026-07-25-phase1b-trusted-result-contract.md`.

## Working Directories

| Role | Path | Branch |
|---|---|---|
| Core worktree | `/Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline` | `feat/phase1a-release-baseline` |
| Core main checkout | `/Users/bowang/IdeaProjects/clousight-bench` | `main` |
| Pro worktree | `/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat` | `feat/phase1a-core-compat` |
| Pro main checkout | `/Users/bowang/IdeaProjects/clousight-bench-pro` | `main` |

`/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench` is a symlink to the Core worktree. The Pro workspace resolves `clousight-bench = { path = "../clousight-bench", editable = true }` through it, so Pro commands run from the Pro worktree see the Core worktree's code.

## File Map

### Pro files created

- `.github/workflows/ci.yml` — the `core-compat` job: check out Pro, check out public Core, `uv sync --frozen`, ruff, pytest, `cb-samplers` wheel + packaged-workload assertion.
- `uv.lock` — newly tracked, so `uv sync --frozen` has a lockfile in CI.

### Pro files modified

- `.gitignore` — stop ignoring `uv.lock`.

### Core files modified (Task 6, after the ruleset exists)

- `SECURITY.md` — replace the "this repository remains private" reporting section.
- `README.md` — state repository status, license and contribution gate.
- `CONTRIBUTING.md` — state the PR + required-checks gate on `main`.
- `docs/architecture.md` — state that Core is public and Pro is a separate private repository.
- `CHANGELOG.md` — record the public-repository fact under `0.2.0`.

---

### Task 1: Confirm Release Identity and Open the Core Phase 1A Pull Request

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline`

**Files:**
- No files change. This task produces GitHub state: a pushed branch and an open pull request.

**Interfaces:**
- Produces: an open PR from `feat/phase1a-release-baseline` into `main` on `clousight/clousight-bench`, with all five check runs queued.
- Consumed by: Task 2 (merge) and Task 5 (required-check context names).

- [ ] **Step 1: Verify the isolated GitHub account is `clousight-dev`**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" auth status
```

Expected: a line reading `Logged in to github.com account clousight-dev` marked `Active account: true`.

If the wrapper reports that its isolated identity is not configured, authenticate that isolated config directly:

```bash
GH_CONFIG_DIR="$HOME/.config/gh-clousight" gh auth login \
  --hostname github.com --git-protocol ssh
/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh auth status
```

Expected after login: the wrapper reports `clousight-dev`; if browser authorization selected any other account, stop and remove only `$HOME/.config/gh-clousight/hosts.yml` before retrying. Do not alter the plain `gh` account.

- [ ] **Step 2: Confirm the Core remote, branch and clean worktree**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline
git remote -v
git branch --show-current
git status --short
```

Expected: `origin` is `https://github.com/clousight/clousight-bench.git`, the branch is `feat/phase1a-release-baseline`, and `git status --short` prints nothing.

If `git status --short` prints anything, stop and resolve it: this plan pushes only committed Phase 1A work.

- [ ] **Step 3: Rebase onto the latest `main`**

Run:

```bash
git fetch origin
git rebase origin/main
git log --oneline origin/main..HEAD | wc -l
```

Expected: the rebase reports `Successfully rebased` or `Current branch ... is up to date`, and the commit count is `21` (verified after the final Phase 1B plan self-review on 2026-07-25).

If the rebase reports conflicts, resolve each conflicted file, `git add` it, and `git rebase --continue`. Abort with `git rebase --abort` if the branch cannot be rebased cleanly, and re-run this step after fixing `main`.

- [ ] **Step 4: Run the full local gate before pushing**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: ruff reports `All checks passed!`; pytest reports all tests passing with only the opt-in network test skipped.

- [ ] **Step 5: Run the installed-wheel smoke exactly as CI will**

Run:

```bash
rm -rf /tmp/csbench-gate-dist /tmp/csbench-gate-venv /tmp/csbench-gate-results
uv build --out-dir /tmp/csbench-gate-dist
uv venv /tmp/csbench-gate-venv --python 3.12
uv pip install --python /tmp/csbench-gate-venv/bin/python /tmp/csbench-gate-dist/*.whl
cd /tmp
/tmp/csbench-gate-venv/bin/csbench list --verbose
/tmp/csbench-gate-venv/bin/csbench run \
  --domain agent-runtime --task T1.3 --platform local-sim \
  --results /tmp/csbench-gate-results
/tmp/csbench-gate-venv/bin/csbench run \
  --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-gate-results
/tmp/csbench-gate-venv/bin/csbench report --results /tmp/csbench-gate-results
cd /Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline
```

Expected: both runs exit `0` and `csbench report` prints a comparison table. This reproduces the `wheel-smoke` CI job locally, so a red CI is not the first time you learn it fails.

- [ ] **Step 6: Push the branch**

Run:

```bash
git push --force-with-lease origin feat/phase1a-release-baseline
```

Expected: the remote branch is updated. `--force-with-lease` is required because Step 3 rebased an already-pushed branch; it refuses to overwrite work you have not seen.

- [ ] **Step 7: Open the pull request**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create \
  --repo clousight/clousight-bench \
  --base main \
  --head feat/phase1a-release-baseline \
  --title "Phase 1A: 0.2.0 developer-preview release baseline" \
  --body "$(cat <<'BODY'
## What

Phase 1A release baseline for the `0.2.0` developer preview.

- Package and runner version reset to `0.2.0`, `Development Status :: 3 - Alpha`.
- Adapter readiness is explicit: `reference` / `experimental` / `wired` / `skeleton`.
  A `skeleton` adapter is discoverable but rejected before preflight.
- Reference workloads (`wordcount-py`, `gsm8k-stats`, `ycsb-wrapper`) are packaged
  under `clousight_bench.resources.workloads` and resolved with
  `core.resources.reference_workload_path()`, so wheel and editable installs
  use the same files.
- CLI input failures (unknown domain/task/platform, skeleton adapter, missing or
  invalid config) return exit code 2 with no traceback.
- CI runs ruff + pytest + a local no-cloud smoke on Python 3.10/3.11/3.12/3.13,
  plus a separate installed-wheel smoke that runs outside the checkout.
- `SECURITY.md` records the current local-execution trust boundary.

## Out of scope

ResultRecord `0.2`, Task `execute`/`score` separation, fingerprints, run plans,
comparability and plugin API ranges. Those are Phase 1B–1D.

## Verification

- `uv run ruff check src tests`
- `uv run pytest -q`
- Installed-wheel smoke outside the checkout: `csbench list --verbose`,
  T1.2/T1.3/T2.1/T4.1/T4.2 on `local-sim`, J1.1 on `local-process`, `csbench report`.
BODY
)"
```

Expected: `gh` prints the new pull request URL.

- [ ] **Step 8: Record the PR number and confirm all five checks are queued**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
CORE_PR="$("$GH" pr list --repo clousight/clousight-bench \
  --state open --base main --head feat/phase1a-release-baseline \
  --json number --jq '.[0].number')"
test -n "$CORE_PR"
"$GH" pr view --repo clousight/clousight-bench "$CORE_PR" \
  --json number,url,headRefName
"$GH" pr checks --repo clousight/clousight-bench "$CORE_PR"
```

Expected: the PR number and URL print, and the wrapper's `pr checks` command lists exactly these five check names (initially `pending`):

```
test (3.10)
test (3.11)
test (3.12)
test (3.13)
wheel-smoke
```

Write the exact five names down; Task 5 uses them verbatim as the required-status-check contexts. If any name differs from this list, use the names the wrapper's `pr checks` command actually printed.

**Rollback:** `/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh pr close --repo clousight/clousight-bench feat/phase1a-release-baseline` closes the PR; `git push origin --delete feat/phase1a-release-baseline` removes the remote branch. The local worktree keeps every commit.

---

### Task 2: Land Core Phase 1A on `main` and Verify the Post-Merge Build

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench/.worktrees/phase1a-release-baseline` and `/Users/bowang/IdeaProjects/clousight-bench`

**Files:**
- No files change. This task produces GitHub state: `main` contains the Phase 1A commits and its CI is green.

**Interfaces:**
- Consumes: the open PR from Task 1.
- Produces: `clousight/clousight-bench` `main` at the Phase 1A merge commit, which Task 3's Pro CI checks out by name.

- [ ] **Step 1: Wait for every check to finish**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr checks --repo clousight/clousight-bench feat/phase1a-release-baseline --watch
```

Expected: the command exits `0` once all five checks report `pass`.

If a check fails, open its log, fix the cause on the branch with a signed-off commit, push, and re-run this step:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
RUN_ID="$("$GH" run list --repo clousight/clousight-bench \
  --branch feat/phase1a-release-baseline --limit 1 \
  --json databaseId --jq '.[0].databaseId')"
test -n "$RUN_ID"
"$GH" run view --repo clousight/clousight-bench "$RUN_ID" --log-failed
```

- [ ] **Step 2: Confirm the merge is clean and mergeable**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr view --repo clousight/clousight-bench feat/phase1a-release-baseline \
  --json mergeable,mergeStateStatus,statusCheckRollup \
  --jq '{mergeable, mergeStateStatus, checks: [.statusCheckRollup[] | {name, conclusion}]}'
```

Expected: `mergeable` is `MERGEABLE`, and every entry in `checks` has `conclusion: "SUCCESS"`.

- [ ] **Step 3: Merge with a merge commit**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr merge --repo clousight/clousight-bench feat/phase1a-release-baseline \
  --merge --delete-branch
```

Expected: `gh` reports the pull request was merged and the head branch was deleted.

Use `--merge`, not `--squash`: every branch commit carries a DCO `Signed-off-by` trailer, and a merge commit preserves those trailers on `main`.

- [ ] **Step 4: Confirm `main` moved and record the merge commit**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git fetch origin
git checkout main
git pull --ff-only origin main
git log --oneline -3
git rev-parse HEAD
```

Expected: `main` now contains the Phase 1A commits; note the SHA printed by `git rev-parse HEAD` — the rollback below needs it.

- [ ] **Step 5: Verify the `main` CI run is green**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
RUN_ID="$("$GH" run list --repo clousight/clousight-bench \
  --branch main --event push --limit 1 \
  --json databaseId --jq '.[0].databaseId')"
test -n "$RUN_ID"
"$GH" run view --repo clousight/clousight-bench "$RUN_ID" \
  --json name,event,headBranch,status,conclusion
"$GH" run watch --repo clousight/clousight-bench "$RUN_ID" --exit-status
```

Expected: the `ci` workflow run on `main` completes with conclusion `success`.

- [ ] **Step 6: Verify a wheel built from `main` runs outside the checkout**

Run:

```bash
rm -rf /tmp/csbench-main-dist /tmp/csbench-main-venv /tmp/csbench-main-results
cd /Users/bowang/IdeaProjects/clousight-bench
uv build --out-dir /tmp/csbench-main-dist
uv venv /tmp/csbench-main-venv --python 3.12
uv pip install --python /tmp/csbench-main-venv/bin/python /tmp/csbench-main-dist/*.whl
cd /tmp
/tmp/csbench-main-venv/bin/python -c "import importlib.metadata as m; assert m.version('clousight-bench') == '0.2.0', m.version('clousight-bench'); print('main wheel is 0.2.0')"
/tmp/csbench-main-venv/bin/csbench run \
  --domain bigdata-emr --task J1.1 --platform local-process \
  --results /tmp/csbench-main-results
```

Expected: `main wheel is 0.2.0` prints and the J1.1 run exits `0`.

**Rollback:** revert the merge on a new branch and land it through a PR, never with a direct push to `main`:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git checkout -b revert/phase1a-release-baseline main
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
MERGE_SHA="$("$GH" pr view --repo clousight/clousight-bench \
  feat/phase1a-release-baseline --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$MERGE_SHA"
git revert -m 1 "$MERGE_SHA" --signoff
git push origin revert/phase1a-release-baseline
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create --repo clousight/clousight-bench --base main \
  --head revert/phase1a-release-baseline \
  --title "Revert Phase 1A release baseline" \
  --body "Reverts the Phase 1A merge commit."
```

---

### Task 3: Add the Pro `core-compat` CI Workflow

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat`

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `uv.lock` (newly tracked; the file already exists on disk but is currently ignored)
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the public Core `main` produced by Task 2.
- Produces: a GitHub check named exactly `core-compat`, used verbatim as Pro's required status check in Task 5.
- Produces: a `workflow_dispatch` input `core_ref` (default `main`) so the Phase 1B plan can run this same job against an unmerged Core branch.

- [ ] **Step 1: Prove the lockfile is absent from a clean clone today**

`uv.lock` exists on disk but is not tracked, so a fresh CI checkout has no lockfile and `uv sync --frozen` cannot work. Reproduce that:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
git ls-files uv.lock
```

Expected: **no output** — the lockfile is untracked, so a clean CI checkout would never receive it and `uv sync --frozen` would have no committed lock to consume.

- [ ] **Step 2: Track the lockfile**

Edit `.gitignore` and delete the single line:

```
uv.lock
```

Leave every other line unchanged. The resulting `.gitignore` is:

```gitignore
__pycache__/
*.pyc
.venv/
dist/
build/
*.egg-info/
.uv/
.pytest_cache/
.ruff_cache/
runs/
results/
.worktrees/
```

Then regenerate the lock so it matches the committed `pyproject.toml` files exactly:

```bash
uv lock
git add -f uv.lock .gitignore
git ls-files uv.lock
```

Expected: `uv.lock` now prints, i.e. it is staged and tracked.

- [ ] **Step 3: Run the exact CI command sequence locally**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
uv sync --all-packages --all-extras --frozen
uv run ruff check packages
uv run pytest -q
```

Expected: the sync resolves from the lockfile without re-locking, ruff reports `All checks passed!`, and every Pro test passes.

The sync works locally because `../clousight-bench` resolves through
`/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/clousight-bench`,
a symlink to the Core worktree. CI reproduces the same layout by checking out
the two repositories as sibling directories.

- [ ] **Step 4: Run the sampler wheel check locally**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
rm -rf dist
uv build --package cb-samplers --out-dir dist
uv run python - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheel = next(Path("dist").glob("cb_samplers-*.whl"))
with ZipFile(wheel) as archive:
    names = set(archive.namelist())
required = {
    "cb_samplers/workloads/synthetic-sampler/manifest.yaml",
    "cb_samplers/workloads/synthetic-sampler/run.py",
}
missing = required - names
assert not missing, f"wheel missing: {sorted(missing)}"
print("cb-samplers workload packaged")
PY
rm -rf dist
```

Expected: `cb-samplers workload packaged`.

- [ ] **Step 5: Create the workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
    inputs:
      core_ref:
        description: "clousight/clousight-bench ref to build against"
        required: false
        default: main

jobs:
  core-compat:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Pro
        uses: actions/checkout@v4
        with:
          path: clousight-bench-pro

      - name: Checkout public Core
        uses: actions/checkout@v4
        with:
          repository: clousight/clousight-bench
          ref: ${{ inputs.core_ref || 'main' }}
          path: clousight-bench

      - name: Install uv
        uses: astral-sh/setup-uv@v5
        with:
          version: "0.7.6"

      - name: Install Python 3.12
        run: uv python install 3.12

      - name: Sync workspace against the checked-out Core
        working-directory: clousight-bench-pro
        run: uv sync --all-packages --all-extras --frozen

      - name: Lint (ruff)
        working-directory: clousight-bench-pro
        run: uv run ruff check packages

      - name: Test (pytest)
        working-directory: clousight-bench-pro
        run: uv run pytest -q

      - name: Build cb-samplers wheel and verify its packaged workload
        working-directory: clousight-bench-pro
        run: |
          rm -rf dist
          uv build --package cb-samplers --out-dir dist
          uv run python - <<'PY'
          from pathlib import Path
          from zipfile import ZipFile

          wheel = next(Path("dist").glob("cb_samplers-*.whl"))
          with ZipFile(wheel) as archive:
              names = set(archive.namelist())
          required = {
              "cb_samplers/workloads/synthetic-sampler/manifest.yaml",
              "cb_samplers/workloads/synthetic-sampler/run.py",
          }
          missing = required - names
          assert not missing, f"wheel missing: {sorted(missing)}"
          print("cb-samplers workload packaged")
          PY
```

The two checkouts are siblings under `$GITHUB_WORKSPACE`, so the workspace source
`clousight-bench = { path = "../clousight-bench", editable = true }` resolves from
`clousight-bench-pro/` to `clousight-bench/` with no path rewriting.

- [ ] **Step 6: Validate the workflow YAML parses**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
uv run python -c "
import yaml, pathlib
doc = yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text(encoding='utf-8'))
assert list(doc['jobs']) == ['core-compat'], list(doc['jobs'])
steps = [s['name'] for s in doc['jobs']['core-compat']['steps']]
assert 'Checkout public Core' in steps, steps
print('workflow ok:', steps)
"
```

Expected: `workflow ok:` followed by the eight step names, proving the job is named `core-compat` — the exact required-check context Task 5 uses.

Also syntax-check the multiline shell, including the Python heredoc after YAML block-scalar de-indentation:

```bash
uv run python - <<'PY'
import pathlib
import subprocess
import yaml

doc = yaml.safe_load(pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
step = next(
    item for item in doc["jobs"]["core-compat"]["steps"]
    if item.get("name") == "Build cb-samplers wheel and verify its packaged workload"
)
script = step["run"]
assert "\nPY\n" in f"\n{script}\n", repr(script)
subprocess.run(["bash", "-n"], input=script, text=True, check=True)
print("workflow heredoc syntax ok")
PY
```

Expected: `workflow heredoc syntax ok`; `bash -n` exits `0`.

- [ ] **Step 7: Commit**

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
git add .github/workflows/ci.yml .gitignore uv.lock
git commit -s -m "ci: verify Pro packages against the public core main"
```

**Rollback:** `git revert --signoff "$(git log -1 --format=%H -- .github/workflows/ci.yml)"` restores the Task 3 commit; re-adding `uv.lock` to `.gitignore` and deleting `.github/workflows/ci.yml` removes the gate entirely. If `uv sync --frozen` starts failing because Core `main` changed version, run `uv lock` and commit the refreshed lockfile.

---

### Task 4: Open and Merge the Pro Phase 1A Pull Request

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat` and `/Users/bowang/IdeaProjects/clousight-bench-pro`

**Files:**
- No files change. This task produces GitHub state: Pro `main` contains the Phase 1A compatibility commits and its `core-compat` check is green.

**Interfaces:**
- Consumes: the workflow from Task 3 and the public Core `main` from Task 2.
- Produces: a green `core-compat` check run on Pro `main`, required by Task 5.

- [ ] **Step 1: Re-confirm the GitHub identity and the private target**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" auth status
"$GH" repo view clousight/clousight-bench-pro --json name,visibility,defaultBranchRef
```

Expected: the active account is `clousight-dev`, and the repository reports `"visibility": "PRIVATE"` with default branch `main`.

If the repository does not resolve, the active account lacks access — go back and switch to `clousight-dev`. Never retarget this PR at `clousight/clousight-bench`.

- [ ] **Step 2: Confirm the branch, remote and clean worktree**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
git remote -v
git branch --show-current
git status --short
git log --oneline origin/main..HEAD
```

Expected: `origin` is `https://github.com/clousight/clousight-bench-pro.git`, the branch is `feat/phase1a-core-compat`, the worktree is clean, and the log shows the `fix: align Pro packages with core 0.2` commit plus the Task 3 CI commit.

- [ ] **Step 3: Push the branch**

Run:

```bash
git fetch origin
git rebase origin/main
git push --force-with-lease origin feat/phase1a-core-compat
```

Expected: the rebase succeeds and the branch is pushed.

- [ ] **Step 4: Open the pull request**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create \
  --repo clousight/clousight-bench-pro \
  --base main \
  --head feat/phase1a-core-compat \
  --title "Phase 1A: align Pro packages with core 0.2 and add core-compat CI" \
  --body "$(cat <<'BODY'
## What

Compatibility-only Phase 1A work. No new commercial capability.

- All four Pro packages pin `clousight-bench>=0.2,<0.3` (`cb-dataservice` keeps
  the `[store]` extra), matching the pre-1.0 open-core version.
- The existing synthetic sampler workload moved into
  `cb_samplers/src/cb_samplers/workloads/` so it ships inside the wheel.
- New `core-compat` CI job: check out Pro and the public
  `clousight/clousight-bench` `main` as siblings, `uv sync --all-packages
  --all-extras --frozen`, `ruff check packages`, `pytest -q`, then build the
  `cb-samplers` wheel and assert the synthetic workload is packaged.
- `uv.lock` is now tracked so `uv sync --frozen` has a lockfile in CI.

## Out of scope

No adapter, enricher, resolver or data-service behaviour changes.
ResultRecord `0.2` contract verification is Phase 1B.
BODY
)"
```

Expected: `gh` prints the pull request URL.

- [ ] **Step 5: Watch the `core-compat` check**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr checks --repo clousight/clousight-bench-pro feat/phase1a-core-compat --watch
```

Expected: exactly one check named `core-compat`, finishing `pass`.

If the sync step fails with a lockfile-out-of-date error, Core `main` drifted from the lock. Fix it on the branch:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro/.worktrees/phase1a-core-compat
uv lock
git add uv.lock
git commit -s -m "chore: refresh lock against the current core main"
git push origin feat/phase1a-core-compat
```

- [ ] **Step 6: Merge**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr merge --repo clousight/clousight-bench-pro feat/phase1a-core-compat \
  --merge --delete-branch
```

Expected: the pull request is merged and the head branch is deleted.

- [ ] **Step 7: Verify Pro `main` and record the merge commit**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git fetch origin
git checkout main
git pull --ff-only origin main
git log --oneline -3
git rev-parse HEAD
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
RUN_ID="$("$GH" run list --repo clousight/clousight-bench-pro \
  --branch main --event push --limit 1 \
  --json databaseId --jq '.[0].databaseId')"
test -n "$RUN_ID"
"$GH" run watch --repo clousight/clousight-bench-pro "$RUN_ID" --exit-status
```

Expected: `main` contains both Phase 1A commits, and the `ci` workflow run on `main` is `success`. Note the merge SHA for rollback.

**Rollback:** revert the merge through a PR, exactly as in Task 2:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench-pro
git checkout -b revert/phase1a-core-compat main
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
MERGE_SHA="$("$GH" pr view --repo clousight/clousight-bench-pro \
  feat/phase1a-core-compat --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$MERGE_SHA"
git revert -m 1 "$MERGE_SHA" --signoff
git push origin revert/phase1a-core-compat
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create --repo clousight/clousight-bench-pro --base main \
  --head revert/phase1a-core-compat \
  --title "Revert Phase 1A core-compat" \
  --body "Reverts the Phase 1A Pro merge commit."
```

---

### Task 5: Apply the `main` Branch Ruleset to Both Repositories

**Repository:** any directory; every command targets GitHub by name.

**Files:**
- No repository files change. This task creates two GitHub repository rulesets.

**Interfaces:**
- Consumes: the check names confirmed in Task 1 (`test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `wheel-smoke`) and Task 3 (`core-compat`).
- Produces: two active rulesets whose ids Task 6 and any rollback need.

- [ ] **Step 1: Verify private-repository ruleset entitlement before changing state**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" api /repos/clousight/clousight-bench/rulesets \
  --jq '[.[] | {id, name, target, enforcement}]'
"$GH" api /repos/clousight/clousight-bench-pro/rulesets \
  --jq '[.[] | {id, name, target, enforcement}]'
```

Current verified result: Core prints `[]`; Pro exits non-zero with HTTP `403` and
`Upgrade to GitHub Pro or make this repository public to enable this feature.`
That is a definitive organization-plan entitlement blocker. Stop here and upgrade
the `clousight` organization to a plan that supports rulesets on private
repositories. Never make Pro public. Re-run this step after the upgrade.

Expected after the entitlement upgrade: both calls exit `0` and return JSON arrays.

- [ ] **Step 2: Confirm no existing ruleset would be duplicated**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
for repo in clousight/clousight-bench clousight/clousight-bench-pro; do
  "$GH" api "/repos/$repo/rulesets" \
    --jq '[.[] | {id, name, target, enforcement}]'
done
```

Expected: `[]` for both. If a `main protection` ruleset already exists, record
its numeric id and use `PUT /repos/clousight/clousight-bench/rulesets/$CORE_RULESET_ID`
or `PUT /repos/clousight/clousight-bench-pro/rulesets/$PRO_RULESET_ID` with the
complete payload from the corresponding step below instead of creating a duplicate.

- [ ] **Step 3: Write and validate the Core ruleset payload**

Create the payload file:

```bash
cat > /tmp/core-main-ruleset.json <<'JSON'
{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "test (3.10)" },
          { "context": "test (3.11)" },
          { "context": "test (3.12)" },
          { "context": "test (3.13)" },
          { "context": "wheel-smoke" }
        ]
      }
    }
  ]
}
JSON
python -m json.tool /tmp/core-main-ruleset.json >/dev/null
```

`"bypass_actors": []` is what makes the rules bind administrators too.
`deletion` blocks branch deletion, `non_fast_forward` blocks force pushes, and
`pull_request` with `required_approving_review_count: 0` requires a pull request
without requiring anyone else's approval.

- [ ] **Step 4: Create the Core ruleset**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" api --method POST /repos/clousight/clousight-bench/rulesets \
  --input /tmp/core-main-ruleset.json \
  --jq '{id, name, enforcement}'
```

Expected: a JSON object with a numeric `id`, `"name": "main protection"` and
`"enforcement": "active"`. Record the id.

- [ ] **Step 5: Write, validate and create the Pro ruleset**

Run:

```bash
cat > /tmp/pro-main-ruleset.json <<'JSON'
{
  "name": "main protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": {
    "ref_name": {
      "include": ["~DEFAULT_BRANCH"],
      "exclude": []
    }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": true,
        "required_status_checks": [
          { "context": "core-compat" }
        ]
      }
    }
  ]
}
JSON

python -m json.tool /tmp/pro-main-ruleset.json >/dev/null
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" api --method POST /repos/clousight/clousight-bench-pro/rulesets \
  --input /tmp/pro-main-ruleset.json \
  --jq '{id, name, enforcement}'
```

Expected: a JSON object with a numeric `id`, `"name": "main protection"` and
`"enforcement": "active"`. Record the id.

- [ ] **Step 6: Verify the effective rules on both default branches**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" api /repos/clousight/clousight-bench/rules/branches/main --jq '[.[].type] | sort'
"$GH" api /repos/clousight/clousight-bench-pro/rules/branches/main --jq '[.[].type] | sort'
```

Expected, for both:

```json
[
  "deletion",
  "non_fast_forward",
  "pull_request",
  "required_status_checks"
]
```

- [ ] **Step 7: Verify the required contexts are exactly the CI check names**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" api /repos/clousight/clousight-bench/rules/branches/main \
  --jq '[.[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context] | sort'
"$GH" api /repos/clousight/clousight-bench-pro/rules/branches/main \
  --jq '[.[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context] | sort'
```

Expected, respectively:

```json
["test (3.10)","test (3.11)","test (3.12)","test (3.13)","wheel-smoke"]
```

```json
["core-compat"]
```

A context that does not match a real check name would block every PR forever,
so a mismatch here must be fixed before Task 6.

- [ ] **Step 8: Verify approvals are not required and admins are not exempt**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
for repo in clousight/clousight-bench clousight/clousight-bench-pro; do
  echo "== $repo"
  "$GH" api "/repos/$repo/rulesets" --jq '.[] | .id' | while read -r id; do
    "$GH" api "/repos/$repo/rulesets/$id" \
      --jq '{bypass_actors, approvals: (.rules[] | select(.type=="pull_request") | .parameters.required_approving_review_count)}'
  done
done
```

Expected, for both repositories: `"bypass_actors": []` and `"approvals": 0`.

**Rollback:** delete the ruleset (this is the only supported way to unblock a
stuck `main`):

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
CORE_RULESET_ID="$("$GH" api /repos/clousight/clousight-bench/rulesets \
  --jq '.[] | select(.name=="main protection") | .id')"
PRO_RULESET_ID="$("$GH" api /repos/clousight/clousight-bench-pro/rulesets \
  --jq '.[] | select(.name=="main protection") | .id')"
test -n "$CORE_RULESET_ID" && test -n "$PRO_RULESET_ID"
"$GH" api --method DELETE "/repos/clousight/clousight-bench/rulesets/$CORE_RULESET_ID"
"$GH" api --method DELETE "/repos/clousight/clousight-bench-pro/rulesets/$PRO_RULESET_ID"
```

Do not attempt a partial `PUT` to change only `enforcement`: ruleset updates are
safer with the complete validated payload. To switch to evaluation mode, copy
the corresponding JSON payload, change `"enforcement": "active"` to
`"enforcement": "evaluate"`, and send that complete file to the recorded id.

---

### Task 6: Correct the Public-Repository Status in Core Documentation

**Repository:** `/Users/bowang/IdeaProjects/clousight-bench`

**Files:**
- Modify: `SECURITY.md:1-12`
- Modify: `README.md:1-10`
- Modify: `CONTRIBUTING.md:1-16`
- Modify: `docs/architecture.md` (the `## 0.2 Developer Preview readiness` section)
- Modify: `CHANGELOG.md` (the `## 0.2.0 — Unreleased` section)

**Interfaces:**
- Consumes: the ruleset created in Task 5 — this pull request is deliberately merged *through* it, which is the end-to-end proof that the required checks and the PR requirement actually work.
- Produces: documentation whose statements about repository visibility, licensing and the merge gate match reality.

- [ ] **Step 1: Prove the current documentation is wrong**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
git checkout main
git pull --ff-only origin main
rg -n "remains private" SECURITY.md
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" repo view clousight/clousight-bench --json visibility --jq .visibility
```

Expected: `SECURITY.md` line 5 says the repository "remains private during the
0.2 developer-preview phase", while the API reports `PUBLIC`. That contradiction
is what this task fixes.

- [ ] **Step 2: Create the documentation branch**

Run:

```bash
git checkout -b docs/public-core-status main
```

- [ ] **Step 3: Rewrite the `SECURITY.md` reporting section**

In `SECURITY.md`, replace the `## Reporting` section (the paragraph beginning
"This repository remains private during the 0.2 developer-preview phase.")
with:

```markdown
## Reporting

Clousight Bench is a public repository. **Do not open a public issue, pull
request or discussion for a suspected vulnerability.** Report it privately
through GitHub Security Advisories:

<https://github.com/clousight/clousight-bench/security/advisories/new>

We acknowledge reports within five working days. Commercial plugins live in a
separate private repository; a vulnerability that only affects a commercial
plugin should be reported through the same advisory form and will be routed
privately.
```

Leave the `## Current trust boundary` section unchanged — it is still accurate.

- [ ] **Step 4: State the repository status in `README.md`**

In `README.md`, insert this block immediately after the `> **0.2.0 Developer
Preview.**` block quote and before the `Run \`csbench list --verbose\`` line:

```markdown
**Repository status.** This repository is public and Apache-2.0 licensed.
`main` is protected: every change lands through a pull request that passes
ruff, pytest and the no-cloud smoke on Python 3.10–3.13 plus a separate
installed-wheel smoke. No approving review is required, force pushes and branch
deletion are blocked, and the rules bind administrators too. Commercial plugins
are developed in a separate private repository and are not required to run
anything in this one.
```

- [ ] **Step 5: State the merge gate in `CONTRIBUTING.md`**

In `CONTRIBUTING.md`, insert this section immediately after the
`## Developer Certificate of Origin (DCO)` section and before `## How to extend`:

```markdown
## How changes land

`main` accepts no direct pushes. Open a pull request; it merges once these
checks pass:

| Check | What it runs |
|---|---|
| `test (3.10)` … `test (3.13)` | `ruff check src tests`, `pytest -q`, and the no-cloud local smoke |
| `wheel-smoke` | builds a wheel, installs it into a clean virtualenv, and runs `csbench` **outside** the checkout |

No approving review is required, but the branch must be up to date with `main`
before merging. Force pushes to `main` and deleting `main` are blocked for
everyone, administrators included.
```

- [ ] **Step 6: Correct `docs/architecture.md`**

In `docs/architecture.md`, in the `## 0.2 Developer Preview readiness` section,
replace the closing paragraph:

```markdown
Phase 1A retains ResultRecord schema `1.0` and plugin API `1.0`. Their `0.2`
replacement is designed but is not implemented until Phase 1B/1D.
```

with:

```markdown
Phase 1A retains ResultRecord schema `1.0` and plugin API `1.0`. Their `0.2`
replacement is designed but is not implemented until Phase 1B/1D.

This repository is public and Apache-2.0 licensed; it contains the whole open
core. Commercial plugins (`cb-pricing`, `cb-samplers`, `cb-dataservice`,
`cb-adapters-enterprise`) live in the separate private `clousight-bench-pro`
repository and attach only through the published entry points
`clousight_bench.domains`, `clousight_bench.enrichers` and
`clousight_bench.asset_resolvers`. Nothing in the open core imports, requires or
degrades without them.
```

- [ ] **Step 7: Record the fact in the changelog**

In `CHANGELOG.md`, inside the `## 0.2.0 — Unreleased` section, append to the
existing `### Changed` list:

```markdown
- The repository is public and Apache-2.0 licensed; `main` is protected by a
  ruleset requiring a pull request and the full CI matrix, with force push and
  branch deletion blocked for everyone. Security reports go through GitHub
  Security Advisories, not public issues.
```

- [ ] **Step 8: Verify no stale "private" claim survives**

Run:

```bash
cd /Users/bowang/IdeaProjects/clousight-bench
rg -n -i "remains private|this repository is private" README.md CONTRIBUTING.md SECURITY.md CHANGELOG.md docs/ ; echo "exit=$?"
```

Expected: no matches and `exit=1`. Matches inside
`docs/superpowers/plans/2026-07-25-phase1a-release-baseline.md` are historical
plan text and are acceptable; if `rg` reports only those, the check passes.

- [ ] **Step 9: Verify the code gate is still green**

Run:

```bash
uv run ruff check src tests
uv run pytest -q
```

Expected: ruff passes and every test passes. Documentation-only changes must not
move these.

- [ ] **Step 10: Commit and push**

```bash
git add SECURITY.md README.md CONTRIBUTING.md docs/architecture.md CHANGELOG.md
git commit -s -m "docs: state the public repository and main protection facts"
git push origin docs/public-core-status
```

- [ ] **Step 11: Open the pull request and confirm the ruleset engages**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create \
  --repo clousight/clousight-bench \
  --base main \
  --head docs/public-core-status \
  --title "docs: state the public repository and main protection facts" \
  --body "Corrects the security reporting channel, records that the repository is public and Apache-2.0, and documents the main-branch merge gate."
"$GH" pr view --repo clousight/clousight-bench docs/public-core-status \
  --json mergeStateStatus,statusCheckRollup \
  --jq '{mergeStateStatus, checks: [.statusCheckRollup[] | {name, conclusion}]}'
```

Expected: `mergeStateStatus` is `BLOCKED` while checks are running — that is the
ruleset from Task 5 doing its job on a real pull request. Five check names must
appear.

- [ ] **Step 12: Merge through the ruleset and verify**

Run:

```bash
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr checks --repo clousight/clousight-bench docs/public-core-status --watch
"$GH" pr merge --repo clousight/clousight-bench docs/public-core-status --merge --delete-branch
cd /Users/bowang/IdeaProjects/clousight-bench
git checkout main
git pull --ff-only origin main
rg -n "Repository status" README.md
rg -n "security/advisories/new" SECURITY.md
```

Expected: the checks pass, the merge succeeds, and both `rg` commands find their
lines on `main`. A successful merge here proves the ruleset accepts a compliant
pull request as well as rejecting a direct push.

**Rollback:** revert through a pull request:

```bash
git checkout -b revert/public-core-status main
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
MERGE_SHA="$("$GH" pr view --repo clousight/clousight-bench \
  docs/public-core-status --json mergeCommit --jq '.mergeCommit.oid')"
test -n "$MERGE_SHA"
git revert -m 1 "$MERGE_SHA" --signoff
git push origin revert/public-core-status
GH=/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh
"$GH" pr create --repo clousight/clousight-bench --base main \
  --head revert/public-core-status \
  --title "Revert public repository status docs" \
  --body "Reverts the documentation merge commit."
```

---

## Phase 1A Delivery Definition of Done

- `/Users/bowang/IdeaProjects/clousight-monitor-extension/scripts/gh.sh auth status` shows `clousight-dev`; no GitHub operation used plain `gh`.
- `clousight/clousight-bench` `main` contains the Phase 1A baseline and its `ci` run is green.
- A wheel built from Core `main` reports version `0.2.0` and runs J1.1 outside the checkout.
- `clousight/clousight-bench-pro` `main` contains the compatibility pins, the packaged sampler workload, a tracked `uv.lock` and the `core-compat` workflow, with a green `ci` run.
- The wrapper's `api /repos/clousight/clousight-bench/rules/branches/main` call reports `deletion`, `non_fast_forward`, `pull_request` and `required_status_checks`.
- Core required contexts are exactly `test (3.10)`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `wheel-smoke`; Pro's is exactly `core-compat`.
- Both rulesets have `bypass_actors: []` and `required_approving_review_count: 0`.
- The documentation pull request merged *through* the Core ruleset, proving the gate works end to end.
- `SECURITY.md` points at GitHub Security Advisories and no longer claims the repository is private; `README.md`, `CONTRIBUTING.md`, `docs/architecture.md` and `CHANGELOG.md` state the public status and the merge gate.
- No version number, ResultRecord field, Task method or adapter changed in this plan.

## Next Plan

With both `main` branches protected and truthful, create the Phase 1B branch from
Core `main` and execute
`docs/superpowers/plans/2026-07-25-phase1b-trusted-result-contract.md`.
