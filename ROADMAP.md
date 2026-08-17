# Roadmap

This is the public roadmap for the Clousight Bench open-source core. It is a
statement of direction, not a commitment to dates. Anything credential-gated
depends on access to real cloud accounts.

Status legend: ✅ done · 🚧 in progress · 📋 planned · 💤 deferred

## Shipped (0.2.0 Developer Preview)

- ✅ Lifecycle orchestrator: `provision → setup → execute → collect → teardown → score → report`
- ✅ Unified `RunSpec` / `ResultRecord` schema with `config_hash` + `runner_version` + `evidence_layer`
- ✅ Entry-point plugin registry: `clousight_bench.domains` / `.enrichers` /
  `.asset_resolvers` / `.runtime_providers` / `.resource_reapers` / `.span_exporters`
- ✅ Cross-language workload protocol (`manifest.yaml` + executable + JSONL on stdout)
- ✅ Three-tier asset resolution (bundled / remote-with-checksum / private-via-resolver)
- ✅ Credential preflight reusing each cloud's default chain; `csbench init` / `csbench doctor`
- ✅ `agent-runtime`: 27 tasks (deploy/teardown, runtime, tools, observability,
  cost, isolation) runnable end-to-end on the `local-sim` reference adapter
- ✅ `bigdata-emr` J1.1 smoke via the `local-process` reference adapter
- ✅ Reports: Markdown + self-contained **HTML/ECharts renderer v2** (bilingual,
  per-dimension matrix + capability matrix + quadrant/time-series/stacked-bar + red flags)
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
- 🚧 **Wire the first real cloud** (`aliyun-agentrun`): the RuntimeProvider, ECI
  probe carrier, resource reaper and Terraform are code-complete and in-tree, and
  the adapter runs end-to-end in `mode: mock`; the live-cloud path is not yet
  validated against a real account (needs a deployed benchmark target + a
  publicly reachable mock endpoint). This is the credibility gate — no real number
  exists yet.
- ✅ **Trusted result contract**: `record_digest` verified via `csbench verify`;
  canonical JSON spec documented in SECURITY.md.
- ✅ Run plans (`--repeat N --warmup W`), summary statistics (mean/stdev/p95),
  and comparability-aware aggregate columns in the report.

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
  `aws-emr`) — skeletons are in-tree; wiring is gated on cloud accounts and
  deployed benchmark targets.
- 💤 Additional domain packs (database / compute / messaging).
- 💤 Security dimensions for `agent-runtime` (code sandbox, egress control,
  credential handling), framed once real-cloud data is available.

## How to influence the roadmap

Open an issue describing the platform, dimension, or domain you want, or the
reproducibility problem you hit. Adding a platform is one adapter file plus one
example config; adding a dimension is one task file with its scoring and declared
evidence layer. See [CONTRIBUTING.md](CONTRIBUTING.md).
