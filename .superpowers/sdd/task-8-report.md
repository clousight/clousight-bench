# Task 8 Report — T2.1/T4.1/T4.2 Execute/Score Migration

## Status

Completed in scope. Migrated tool registration, trace completeness, and OTel
export tasks from task-specific `run()` implementations to the Phase 1B
`execute()`/`score()` contract while preserving legacy metric keys and bridge
`ok` semantics.

## Implementation

- T2.1 records all registration path attempts as raw support observations and
  scores supported paths plus the stable no-registration-path finding.
- T4.1 records trace capability, tool-call count, spans, and unsupported reason;
  scoring derives completeness, present/missing span kinds, and stable findings.
- T4.2 records OTel capability, payload, and unsupported reason; scoring derives
  payload validity, span count, validation problems, and stable findings.
- All three tasks declare `task_revision = "2"` and `scorer_revision = "2"`.
- Added non-sensitive environment hooks for probed registration paths, trace
  completeness policy, and OTel export policy.
- Kept measurement keys byte-identical to the old task metric keys. Warning and
  info findings do not change the bridge's historical `ok=True` behavior.

## TDD Evidence

1. Appended the five specified contract tests before modifying production code.
2. The initial focused run failed all five tests for the expected missing
   `execute()` behavior, including `NotImplementedError` for each migrated task.
3. Implemented the three migrations and reran the focused tests; all five
   passed.

## Verification

- Focused Task 8 tests:
  `uv run pytest tests/test_task_contract_agent_runtime.py -v -k "t2_1 or t4_1 or t4_2"`
  — 5 passed, 8 deselected.
- Contract, bridge, and preflight regression:
  `uv run pytest tests/test_task_contract_agent_runtime.py
  tests/test_agent_runtime_dimensions.py tests/test_preflight.py -v`
  — 36 passed.
- Full suite: `uv run pytest -q` — 165 passed, 1 skipped.
- Lint: `uv run ruff check src tests` — all checks passed.
- IDE diagnostics: no errors in the four changed code/test files.
- Patch hygiene: `git diff --check` — clean.

## Self-review

- Adapter, session, mock-server, and network interactions remain exclusively in
  `execute()`; each `score()` reads only its supplied observation bundle.
- Scorers copy the raw collections they derive from and do not mutate the
  observation bundle, adapter state, or external resources.
- Structured observations contain no verdict metric keys; measurements and
  findings are produced only during scoring.
- Legacy dimension tests confirm byte-identical metric names and values. The
  temporary bridge continues to mark unsupported, partial, and invalid
  capability results `ok=True` because their findings are non-critical.
- No orchestrator cutover, unrelated task migration, or existing legacy test
  edits were introduced.

## Concerns

None blocking. Until the later orchestrator cutover, the temporary bridge still
flattens rich measurements and omits findings from legacy result records; this
is existing planned Phase 1B behavior.
