# Changelog

All notable changes to Clousight Bench are recorded here.

## 0.2.0 — 2026-08-17

Developer-preview reset; the first public release.

### Added

- Cost & cleanup closed loop (builds on the live gate): a **cumulative cost
  budget** — `--cost-budget` / `CSBENCH_COST_BUDGET` / `target.cost_budget` caps
  total realized spend across runs sharing a `--results` dir; a billable run that
  would cross it stops before provisioning (`cost.budget_exceeded`), realized
  cost (priced by the enricher, else `target.estimated_cost_usd`) accruing to
  `<results>/.cost_ledger.json`. **Resource tagging applied end-to-end**: the
  shared managed adapter now stamps `resource_tags()` on every resource it
  provisions (all four clouds inherit it) and books it in a per-run
  `ResourceLedger` (`<results>/.resource_ledger.jsonl`). **Post-run
  reconcile-by-tag**: after every run the orchestrator reverse-looks-up the
  run's residual (local ledger + a `ResourceReaper.verify(run_id)` cloud tag
  query when installed), destroys what it can, and reports — `teardown.reclaimed`
  (a leak the harness cleaned) or `teardown.residual` (critical, could not
  reclaim → `csbench sweep`). Closes the "did the run leave anything billing?"
  gap that self-reported teardown could not answer.
- Pre-access hardening (safety belt before the first real-cloud wiring): a
  **live-run cost gate** — a run whose numbers come from a real cloud
  (`execution_mode == "live"` with a real provider) refuses to provision unless
  the operator acknowledges cost via `--allow-live` / `CSBENCH_ALLOW_LIVE`,
  producing an `invalid` record with a `live.unconfirmed` finding before SETUP;
  simulated / provider-less runs are never gated, and an acknowledged live run
  records `extensions.core.live_run` (with any `target.live_limits`). **Run-id
  resource tagging** (`core.resource_tags`, `ProviderAdapter.resource_tags()`)
  so a crashed run's orphaned cloud resources are findable, plus a
  `ResourceReaper` plugin seam and `csbench sweep --provider <p> [--confirm]`
  that reconciles them (open-core ships no reaper and fails clearly). **Cloud
  account scrub** (`redaction.scrub_cloud_identifiers`): every stage-error
  message is stripped of embedded ARNs / account ids before it is stored, so a
  published record never leaks the operator's cloud account. A shared
  **`ClientPolicy`** (timeouts + retry/backoff on `ClientContext`, resolved from
  `target.timeouts` / `target.retries`) that all four clouds inherit, bounded by
  the run's remaining deadline (`bounded_read_timeout`) since the SIGALRM stage
  deadline cannot interrupt a threaded load probe. Optional
  **`X-Clousight-Token` auth** on the mock tool server (`--token` /
  `CSBENCH_MOCK_TOKEN`) for when it must be tunnel-exposed to a cloud runtime.
  Complete provision/deprovision RAM/IAM maps for Huawei & Volcengine, and the
  AgentRun integration research doc (`docs/agentrun-integration-research.md`)
  the Aliyun adapter cites.
- Phase 1D plugin & contract hardening (stability slice): plugins declare a
  `requires_plugin_api` range (`core.versioning`, zero-dependency) and the
  registry hard-rejects one that excludes this core (`IncompatiblePluginError`)
  or two plugins that claim the same domain / enricher / provider / exporter /
  resolver name or the same task_id / platform within a domain
  (`DuplicatePluginError`) — no more silent last-wins. Authoritative JSON
  Schemas for RunSpec / workload manifest / ResultRecord ship in the wheel and
  are validated with the optional `[validate]` extra (`jsonschema`), falling
  back to the hand-written checks when it is absent; a record that fails the
  0.2 schema is refused at PERSIST and emergency-dumped raw rather than lost.
  `csbench conformance --domain <d> [--platform]` checks an installed domain
  against the contract (both built-in domains pass in CI). Workload sandboxing
  and path/URI allow-listing remain out of scope for this slice.
- `agent-runtime`: three more dimensions on the 0.2 contract, for eight total on
  `local-sim` — T1.1 cold/warm start latency, T5.1 cost attribution (emits usage
  measurements the pricing enricher prices), T5.2 elasticity under concurrency.
  Adapters gain a `managed`/`transport`/`mode` split (`core.clients` /
  `core.endpoints` helpers) so the same code drives the local simulated runtime
  or, once wired, a real cloud; the report gains a platform × capability matrix.
- Open cost attribution: the usage vocabulary (`core.usage`) and a reference
  cost enricher (`clousight_bench.enrichers.pricing`, registered via the
  `clousight_bench.enrichers` entry point) now ship in the core, with a small
  bundled seed of public list prices. It only prices records that report usage
  and never overwrites a cost another enricher already computed; point
  `CLOUSIGHT_PRICING_DATA` at a fuller/fresher feed to override the data without
  forking the mechanism.
- Reproducible sampling: `core.sampling.HighFreqSampler` (the `sample`-event
  protocol helper) and a `synthetic-sampler` reference workload.
- `csbench rollup <run_dir> [--bucket-s N]` downsamples a run's `series.parquet`
  into `series_rollup.parquet` (avg/p99/max/count per bucket); needs `[store]`.
- Result store & analytics (optional `[store]` extra): high-frequency `series`
  are written as `series.parquet` sidecars, and `csbench query "<sql>"` /
  `csbench export <view> --out f.parquet` run DuckDB SQL over flattened
  `records` / `measurements` / `findings` / `series` views for cross-cloud
  analysis or notebook/BI export (see `docs/querying.md`). Cost is surfaced on a
  **list → discount → net** axis (`CLOUSIGHT_PRICING_DATA` / `CLOUSIGHT_PRICING_DISCOUNTS`).
- Reporting: alongside the Markdown report, a self-contained **HTML/ECharts
  renderer** (now the default) with bilingual labels, a per-dimension matrix,
  capability matrix and quadrant / time-series / stacked-bar panels, plus a cost
  column and red flags — no external assets, one openable file.
- `docs/dataset-tiers.md`: the open-seed vs. private-held-out dataset policy.
- Project is a typed package: ships a `py.typed` marker (PEP 561) so downstream
  consumers get type information; CI enforces `mypy` on the source.
- Community health files: `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1),
  `GOVERNANCE.md`, `MAINTAINERS.md`, `ROADMAP.md`, `NOTICE`, GitHub issue forms,
  a pull-request template, `.github/CODEOWNERS`, and Dependabot.
- Tag-triggered PyPI release workflow using Trusted Publishing (OIDC); see
  `docs/RELEASING.md`. Test coverage is measured (`pytest --cov`) and a
  MkDocs + mkdocstrings site (`docs/`, built `--strict` in CI) is added.
- `[project.urls]` metadata and per-version Python classifiers for PyPI.

### Changed

- Restored installable reference workloads and wheel smoke coverage.
- Made adapter implementation status explicit.
- Standardized user-facing CLI configuration errors.
- A relative reference-workload name (e.g. `workload: wordcount-py`) now
  resolves against the packaged `clousight_bench.resources.workloads` tree via
  `core.resources.reference_workload_path()`, not a repository-relative path.
  If you point a task at your own workload, pass an **absolute path** —
  relative paths that used to reach `workloads/<name>/` in a checkout no
  longer do.
- Unknown domain/task/platform lookups and a rejected skeleton adapter now
  raise typed `UnknownDomainError` / `UnknownTaskError` / `UnknownPlatformError`
  / `AdapterNotRunnableError` (all `UserInputError` subclasses) instead of a
  bare `KeyError`, so callers of the Python API get a stable, catchable
  exception hierarchy — the same one the CLI maps to exit code 2.
- The repository is public and Apache-2.0 licensed; `main` is protected by a
  ruleset requiring a pull request and the full CI matrix, with force push and
  branch deletion blocked for everyone. Security reports go through GitHub
  Security Advisories, not public issues.

### Fixed

- `csbench doctor --domain --platform` no longer hard-rejects a skeleton
  adapter before showing anything: it prints a clear "skeleton, not
  implemented" warning and still runs `adapter.preflight(task)`, so the
  credential/SDK/minimal-permission requirements a contributor needs before
  wiring the adapter are visible. `csbench run`'s hard skeleton gate (exit
  code 2, checked in the orchestrator before preflight) is unchanged.
- `--config` pointed at a directory, an unreadable file, or a non-UTF-8 file
  now fails with the same stable `UserInputError` / exit-code-2 usage error as
  a missing file or invalid YAML, instead of an unhandled
  `IsADirectoryError` / `PermissionError` / `UnicodeDecodeError` traceback.
- The `examples/README.md` J1.1 walkthrough no longer references
  `configs/bigdata-emr.local.yaml`, which was never created; the command runs
  with the packaged default workload and no `--config`.

### Added

- Schema `0.2` result contract with `identity`, `environment`, `fingerprints`,
  `measurements`, `findings`, `observations`, `errors` and a four-value
  `status`.
- Deterministic `benchmark`, `environment` and `implementation` fingerprints
  plus a `record_digest`, all full SHA-256 over a canonical JSON encoding.
- `Task.execute()` / `Task.score()`, so a stored observation can be re-scored
  without re-running the benchmark.
- Atomic result persistence with an emergency dump into the system temp
  directory when the results directory cannot be written.
- `csbench migrate-results SOURCE --output DEST [--dry-run]` and
  `csbench run --debug`.
- A minimal `ResultPublisher` boundary with append-only publish receipts. Core
  ships no publisher.
- Run plans: `csbench run --repeat N --warmup W` executes the benchmark
  `warmup + repeat` times (each still its own auditable `0.2` record), discards
  the warmups, and writes a `run_plan_aggregate` under `results/aggregates/`.
- Statistical aggregation over repeats (`core/statistics.py`): numeric
  measurements get `n`, `mean`, `stdev`, `min`, `max`, `p50`, `p95` and `cv`;
  label measurements get their distribution, `mode` and `agreement`.
- Comparability-aware reporting: `csbench report` pools only records that share
  a `benchmark` **and** `environment` fingerprint, and flags a cell that mixes
  benchmarks (not comparable) or implementation fingerprints (comparable only
  with the caveat that the code changed).

### Changed (breaking)

- `ResultRecord` moved from schema `1.0` to `0.2`. `ok`, top-level `metrics`,
  top-level `evidence_layer` and `config_hash` are gone; migrate old files with
  `csbench migrate-results`.
- `Task.run()` and `TaskOutput` are removed. Implement `execute()` and
  `score()`.
- `clousight_bench.core.schema.config_hash` and
  `clousight_bench.core.schema.EVIDENCE_LAYERS` are removed; fingerprints and
  `core.observation.EVIDENCE_LAYERS` replace them.
- `csbench run` exit codes: `0` for `completed` and `unsupported`, `1` for
  `failed` and `invalid`, `2` for a user input error. A failed run used to exit
  `2`.
- An enricher failure is now isolated: it records an ENRICH stage error and
  leaves `status` alone instead of aborting the run.

### Compatibility

- Package version is pre-1.0.
- Result schema is now `0.2` (migrate `1.0` files with `csbench migrate-results`).
- Plugin API is `1.0`; Phase 1D (in this release) adds version-range negotiation
  and conflict detection around it without bumping the version.
