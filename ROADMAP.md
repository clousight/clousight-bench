# Roadmap

This is the public roadmap for the Clousight Bench open-source core. It is a
statement of direction, not a commitment to dates. Anything credential-gated
depends on access to real cloud accounts.

Status legend: ✅ done · 🚧 in progress · 📋 planned · 💤 deferred

## Shipped (0.3.0 Developer Preview)

- ✅ Lifecycle orchestrator: `provision → setup → execute → collect → teardown → score → report`
- ✅ Unified `RunSpec` / `ResultRecord` schema with provenance-folded fingerprints, `reproducibility_class` + `official` per measurement (schema 0.3)
- ✅ Entry-point plugin registry: `clousight_bench.domains` / `.enrichers` /
  `.asset_resolvers` / `.runtime_providers` / `.resource_reapers` / `.span_exporters`
- ✅ Cross-language workload protocol (`manifest.yaml` + executable + JSONL on stdout)
- ✅ Three-tier asset resolution (bundled / remote-with-checksum / private-via-resolver)
- ✅ Credential preflight reusing each cloud's default chain; `csbench init` / `csbench doctor`
- ✅ `agent-runtime`: fault-injectable mock tool server, `local-sim` adapter, latency-class data-plane probe seam, reliability group (fault injection via mock server, three-state platform attribution), in-tree `aliyun-agentrun` + `aws-agentcore` runtime providers + ECI probe carrier + reaper + Terraform
- ✅ Result store & analytics: Parquet series sidecars + DuckDB-backed `csbench query`
  (optional `[store]` extra)

### Cost, safety & cleanup (pre-real-cloud safety belt)

- ✅ Open cost attribution: usage vocabulary (`core.usage`) + reference `pricing`
  enricher; cumulative **cost budget** (`--cost-budget`) with a per-`--results`
  ledger that stops a billable run before it crosses the cap
- ✅ **Live-run cost gate**: a run whose numbers come from a real cloud refuses to
  provision without `--allow-live`
- ✅ **Resource tagging + reconciliation**: every provisioned resource is run-id
  tagged; post-run reconcile-by-tag + a `ResourceReaper` seam and `csbench sweep`
  find and reap orphans (open core ships no reaper by default and fails clearly)
- ✅ Cloud-account scrub on stored errors; shared `ClientPolicy` (timeouts/retries);
  optional `X-Clousight-Token` mock-server auth

## Near-term (open-source core)

- 🚧 Broaden the `agent-runtime` measurement layer (latency distributions, usage
  metrics, capability matrix) and add more dimensions on the mock runtime.
- ✅ **First real-cloud run** (`aliyun-agentrun`, now `experimental`): the in-tree
  RuntimeProvider + ECI probe carrier + reaper + Terraform ran a real-cloud
  campaign (`cn-hangzhou`, 2026-08-15 — 25 `completed` + 2 honestly
  `unsupported`). The credibility gate is crossed: the first real, provenance-attributed
  numbers exist.
- 🚧 **Harden the live path toward `wired`**: repeat the campaign across
  regions/accounts, watch for contamination, and promote `aliyun-agentrun` from
  `experimental` to `wired` once the live path is stable and reproducible.
- ✅ **Trusted result contract**: `record_digest` verified via `csbench verify`;
  canonical JSON spec documented in SECURITY.md.
- ✅ Run plans (`--repeat N --warmup W`), summary statistics (mean/stdev/p95),
  and comparability-aware aggregate columns in the report.

## Benchmark-suite / evaluator contract (2026-08, shipped — slice 1)

The `benchmark_suite` / `evaluator` plugin contract (Sub-project B, slice 1) is
complete and green:

- ✅ **Contract ABCs** (`BenchmarkSuite`, `Evaluator`, `RawArtifacts`, `DatasetHandle`,
  `EnvHandle`, `Target`, `DriverContext`, `Telemetry`) with full mypy types.
- ✅ **Suite task + provenance**: `SuiteTask` threads `Provenance` (suite_id, suite_version,
  evaluator_id, unmodified) through every `TaskResult`; folded into the benchmark fingerprint.
- ✅ **SWE-bench Verified pilot**: `SweBenchSuite` (registered as `swe-bench` entry point) +
  `MockAgent` (gold / empty / random patch kinds) + bundled fixtures (predictions, results,
  trajectory, usage) + `[swebench]` optional extra gating the real Docker path.
- ✅ **Official evaluator**: `OfficialSweEvaluator` emits `swe-bench.resolved` (ratio,
  deterministic) and `swe-bench.cost_per_resolved` (usd, environmental) under the
  `swe-bench.` namespace; both `official=True`.
- ✅ **SUT span schema** (`core/sut_span.py`, v2: trace_id/status) for recording the
  SUT's trajectory as auditable data alongside the benchmark result, rendered by the
  web viewer's transcript + waterfall views.
- ✅ **Conformance checks**: `conformance.check_evaluator(evaluator, suite_id, measurements)`
  verifies the namespace/official invariant — official evaluators emit only `official=True`
  keys under `"<suite_id>."`, custom evaluators only `official=False` under their own id.
- ✅ **Gated real-Docker smoke** (`@pytest.mark.slow`): exercises `SweBenchSuite.run()` with
  `agent_kind="gold"` (→ resolved=1) and `agent_kind="empty"` (→ resolved=0); deselected
  from the default fast gate; requires `[swebench]` extra + Docker.

## Real-cloud SWE-bench + driver host (2026-08, code complete)

- ✅ **Real dataset pin**: SWE-bench Verified at a real HF revision with the dataset's
  real gold patches bundled as fixtures (golden fingerprint pin guards identity).
- ✅ **Real SUT path**: the suite's non-mock run invokes an AgentRun-hosted agent per
  issue (`oracle` pipeline-validation mode / `llm` DashScope mode) and captures REAL
  trajectory spans + token usage — `swe-bench.cost_per_resolved` is computed from
  real usage.
- ✅ **Docker-capable ECS driver host (Sub-project A)**: terraform knobs (docker,
  disk, registry mirror, HF mirror), suite-aware `LaunchSpec`, `csbench submit`
  drives `suite:swe-bench` in-region; OSS-only control plane + self-destruct reaper.
- ✅ Live smoke plan + bilingual runbook (`docs/swe-bench-live-runbook.mdx`).
- 📋 **First live SWE-bench smoke** — gated on account preconditions (balance,
  terraform credentials, ACR registry mirror), not on code.

## Web viewer (Sub-project C, shipped)

- ✅ `csbench serve`: React + Vite UI (prebuilt `dist/` shipped in the wheel — users
  need no node), record list/detail with provenance cards, trace transcript +
  ECharts waterfall, EN | 中文, dark/light, strict CSP, offline-first, read-only,
  localhost-bound with Host-header guard.

## Suite-first pivot (2026-08, done)

The self-designed 27-task agent-runtime T-code suite and the `bigdata-emr` domain
were retired. Benchmark jobs are now driven by the `benchmark_suite` /
`evaluator` contract (Sub-project B, shipped) — the harness ingests externally-
defined suites (e.g. SWE-bench) rather than hand-coded dimensions. The live-validated
cloud infra (runtime providers, ECI probe, reaper, carriers, Terraform) is **kept**
and carries over for the SWE-bench pilot. Results remain canonical JSON records;
visualization is deferred to the Sub-project C web viewer.

- 💤 `agent-runtime` 27-task T-code suite — retired; suite-driven runs (`suite:<id>`) replaced them
- 💤 `bigdata-emr` domain (J1.1 smoke + `aws-emr` skeleton) — retired; the cross-language workload protocol is kept
- 💤 HTML/ECharts report renderer (`csbench report`) — retired; Sub-project C web viewer handles visualization

## Later

- ✅ **Plugin contract hardening (Phase 1D, stability slice)**: plugin API
  version ranges + conflict detection, JSON Schema for `RunSpec` / `ResultRecord`
  / workload manifest (validated at VALIDATE / manifest-load / PERSIST), and a
  `csbench conformance` kit for third-party plugins.
- ✅ **Workload sandbox, layers 1+2 (security-relevant)**: path-traversal
  protection (artifact + bundled asset + symlink escape), POSIX resource limits
  on the workload subprocess, and https-only + SSRF-guarded asset URIs.
- 📋 **Workload sandbox, layers 3-5**: filesystem / network / process isolation
  for hostile code. Until this lands there is no strong isolation against a
  determined adversary — review workloads you do not trust (see
  [SECURITY.md](SECURITY.md)).
- 💤 Remaining real-cloud adapters (`huawei-agentarts`, `volcengine-agentkit`,
  `aws-agentcore`) — skeletons are in-tree; wiring is gated on cloud accounts and
  deployed benchmark targets.
- 💤 Additional domain packs (database / compute / messaging).
- 💤 Security dimensions for `agent-runtime` (code sandbox, egress control,
  credential handling), framed once real-cloud data is available.

## How to influence the roadmap

Open an issue describing the platform, dimension, or domain you want, or the
reproducibility problem you hit. Adding a platform is one adapter file plus one
example config; adding a suite is one `BenchmarkSuite` + one `Evaluator` plugin
(the SWE-bench pilot is the template). See [CONTRIBUTING.md](CONTRIBUTING.md).
