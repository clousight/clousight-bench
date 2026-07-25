# Task 7 Report — T1.2/T1.3 Execute/Score Migration

## Status

Completed in scope. Migrated T1.2 state persistence and T1.3 fault recovery
from task-specific `run()` implementations to the Phase 1B
`execute()`/`score()` contract while preserving the legacy metric keys and
bridge behavior.

## Implementation

- T1.2 now records capability, probe, recovered state, and unsupported reasons
  as raw observations; scoring derives persistence measurements and stable
  findings.
- T1.3 now records the configured fault, plan size, completion state, and every
  tool attempt; scoring derives recovery measurements and stable findings.
- Both tasks declare `task_revision = "2"` and `scorer_revision = "2"`.
- Added non-sensitive environment facts for state persistence and recovery
  policies, including the retry budget.
- Kept all legacy measurement key names byte-identical so the temporary Task 6
  bridge continues to populate existing `TaskOutput.metrics`.
- Added contract coverage for observation/verdict separation, pure repeatable
  scoring, findings, attempt capture, and environment hooks.

## TDD Evidence

1. Added `tests/test_task_contract_agent_runtime.py` before modifying either
   production task.
2. Initial focused run failed all eight tests for the expected missing
   `execute()`, `score()`, and environment hook behavior, including
   `NotImplementedError: StatePersistenceTask must implement execute()`.
3. Implemented only the required task migrations and reran the focused suite;
   all eight tests passed.

## Verification

- New contract tests:
  `uv run pytest tests/test_task_contract_agent_runtime.py -v` — 8 passed.
- Contract + legacy bridge smoke:
  `uv run pytest tests/test_task_contract_agent_runtime.py
  tests/test_agent_runtime_local.py tests/test_agent_runtime_dimensions.py -v`
  — 20 passed.
- Full suite: `uv run pytest -q` — 160 passed, 1 skipped.
- Lint: `uv run ruff check src tests` — all checks passed.
- IDE diagnostics: no errors in the three changed code/test files.
- Patch hygiene: `git diff --check` — clean.

## Self-review

- `execute()` contains adapter and network interactions but no verdict fields;
  `score()` only reads the supplied observation bundle.
- Repeated T1.2 scoring leaves the bundle unchanged and produces the same
  result.
- Unsupported state APIs remain successful observations in the legacy bridge;
  fail-fast recovery remains a warning rather than a critical run failure.
- A missing injected fault is now represented by the required critical finding,
  preserving the old `ok=False` bridge behavior.
- No orchestrator cutover, unrelated task migration, or legacy test edits were
  introduced.

## Concerns

None blocking. Until the later orchestrator cutover, the temporary Task 6 bridge
still flattens rich measurements and does not expose findings in legacy result
records; this is existing planned Phase 1B behavior.
