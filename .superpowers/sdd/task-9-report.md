# Task 9 Report — J1.1 Execute/Score Migration

## Status

Completed in scope. Migrated J1.1 from its task-specific `run()` implementation
to the Phase 1B `execute()`/`score()` contract while preserving workload metric
values, `job_succeeded`, legacy bridge `ok`, and auditable raw job output.

## Implementation

- `execute()` now records workload name, effective job parameters, raw workload
  metrics, exit code, success flag, bounded logs, series, and artifacts without
  producing scored conclusions.
- `score()` promotes every workload metric to an evidence-layer-C
  `Measurement`, adds `job_succeeded`, and emits the stable critical finding
  `bigdata.job_failed` for unsuccessful jobs.
- Declared `task_revision = "2"` and `scorer_revision = "2"`.
- `workload_identity()` now uses `WorkloadEngine.describe()` to include the
  packaged workload name, version, and asset identities in the benchmark
  fingerprint.
- `environment_facts()` declares the non-sensitive workload name.
- Kept packaged-resource resolution and the temporary legacy `run()` bridge
  compatible.

## TDD Evidence

1. Added the five specified J1.1 contract tests before modifying production
   code.
2. The initial focused run failed all five tests for the expected missing
   contract behavior: `execute()` and `score()` raised `NotImplementedError`,
   while workload identity and environment facts returned their defaults.
3. Implemented the migration and reran the focused and related tests; all eight
   passed.

## Verification

- Focused and related:
  `uv run pytest tests/test_task_contract_bigdata.py
  tests/test_bigdata_workload.py -v` — 8 passed.
- CLI bridge:
  `uv run csbench run --domain bigdata-emr --task J1.1
  --platform local-process --results /tmp/csbench-phase1b-j11` — exit 0,
  legacy-shaped record emitted with `ok=true` and preserved metrics/raw output.
- Full suite: `uv run pytest -q` — 170 passed, 1 skipped.
- Lint: `uv run ruff check src tests` — all checks passed.
- IDE diagnostics: no errors in the changed source and test files.
- Patch hygiene: `git diff --check` — clean.

## Self-review

- Adapter submission and workload execution remain exclusively in `execute()`;
  `score()` reads only its supplied observation bundle.
- Raw workload metrics do not contain `job_succeeded`; that conclusion is
  created only during scoring.
- Metric names and values remain unchanged through the bridge, and a successful
  workload still yields legacy `ok=True`.
- Failed jobs retain exit code and logs as finding details and become
  bridge-visible failures through a critical finding.
- Workload identity is derived from the same packaged or explicitly selected
  workload directory that execution uses, including declared asset identities.
- No orchestrator cutover, unrelated task migration, or existing test edits
  were introduced.

## Concerns

None blocking. Until the planned orchestrator cutover, the temporary bridge
still flattens measurements and does not serialize findings into legacy result
records; this is existing Phase 1B behavior.
