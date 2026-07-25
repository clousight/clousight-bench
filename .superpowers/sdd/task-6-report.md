# Task 6 Report — Execute/Score Task Contract

## Status

Completed in scope. Added the `Task.execute`/`Task.score` contract, revision and
fingerprint hooks, and the temporary `TaskOutput` bridge while preserving the
legacy orchestrator and existing built-in tasks.

## Implementation

- Added default `task_revision` and `scorer_revision` values of `"0"`.
- Added loud default `execute()` and `score()` failures for unmigrated concrete
  tasks that implement neither contract method.
- Added the non-sensitive `environment_facts()` hook with an empty default.
- Added `workload_identity()` with exactly the default keys `workload`,
  `workload_version`, and `assets`.
- Converted `run()` from an abstract method into the temporary bridge:
  `execute` → `collect` → `score` → legacy `TaskOutput`.
- Preserved old task behavior because built-in tasks continue to override
  `run()`; no orchestrator cutover or TaskOutput removal was introduced.

## TDD Evidence

1. Added `tests/test_task_contract.py` before changing production code.
2. Initial focused run produced the expected five failures: missing revision
   attributes and inability to instantiate tasks while `run()` remained
   abstract.
3. After the minimal implementation, all five contract tests passed.

## Verification

- Focused contract: `uv run pytest tests/test_task_contract.py -v` — 5 passed.
- Contract + orchestrator bridge:
  `uv run pytest tests/test_task_contract.py tests/test_orchestrator_series_bridge.py -q`
  — 6 passed.
- Full suite: `uv run pytest -q` — 152 passed, 1 skipped.
- Lint: `uv run ruff check src tests` — all checks passed.
- Patch hygiene: `git diff --check` — clean.

## Self-review

- The bridge validates observations through `collect()` before scoring.
- Measurements, raw observations, notes, series, and artifacts are mapped to
  the legacy output; any critical finding marks the output not OK.
- Existing tasks and orchestrator code were not modified.
- No Task 12 cutover work was pulled forward: `TaskOutput` remains and the
  orchestrator continues to call `run()`.

## Concerns

None blocking. The bridge intentionally exposes only measurement values in
legacy `metrics`; richer measurement metadata and findings remain part of the
new `TaskResult` contract for the later cutover.
