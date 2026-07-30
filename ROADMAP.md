# Roadmap

This is the public roadmap for the Clousight Bench open-source core. It is a
statement of direction, not a commitment to dates. Anything credential-gated
depends on access to real cloud accounts.

Status legend: ✅ done · 🚧 in progress · 📋 planned · 💤 deferred

## Shipped (0.2.0 Developer Preview)

- ✅ Lifecycle orchestrator: `provision → setup → execute → collect → teardown → score → report`
- ✅ Unified `RunSpec` / `ResultRecord` schema with `config_hash` + `runner_version` + `evidence_layer`
- ✅ Entry-point plugin registry (`clousight_bench.domains` / `.enrichers` / `.asset_resolvers`)
- ✅ Cross-language workload protocol (`manifest.yaml` + executable + JSONL on stdout)
- ✅ Three-tier asset resolution (bundled / remote-with-checksum / private-via-resolver)
- ✅ Credential preflight reusing each cloud's default chain; `csbench init` / `csbench doctor`
- ✅ `agent-runtime` dimensions runnable end-to-end on the `local-sim` reference adapter
- ✅ `bigdata-emr` J1.1 smoke via the `local-process` reference adapter
- ✅ Markdown comparison report (per-dimension matrix + capability matrix + red flags)

## Near-term (open-source core)

- 🚧 Broaden the `agent-runtime` measurement layer (latency distributions, usage
  metrics, capability matrix) and add more dimensions on the mock runtime.
- 📋 **Trusted result contract**: stronger, fully-hashed result fingerprints and a
  deterministic results-migration path, so every published number stays
  independently verifiable.
- 📋 Run plans (warmup / repeats), summary statistics (median / p95 / p99), and
  comparability-aware reporting.

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
- 💤 Real-cloud adapters (`aliyun-agentrun`, `huawei-agentarts`,
  `volcengine-agentkit`, `aws-emr`) — skeletons are in-tree; wiring is gated on
  cloud accounts and deployed benchmark targets.
- 💤 Additional domain packs (database / compute / messaging).
- 💤 Security dimensions for `agent-runtime` (code sandbox, egress control,
  credential handling), framed once real-cloud data is available.

## How to influence the roadmap

Open an issue describing the platform, dimension, or domain you want, or the
reproducibility problem you hit. Adding a platform is one adapter file plus one
example config; adding a dimension is one task file with its scoring and declared
evidence layer. See [CONTRIBUTING.md](CONTRIBUTING.md).
