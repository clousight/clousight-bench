# Clousight Bench

[![CI](https://github.com/clousight/clousight-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/clousight/clousight-bench/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/clousight-bench.svg)](https://pypi.org/project/clousight-bench/)
[![Python](https://img.shields.io/pypi/pyversions/clousight-bench.svg)](https://pypi.org/project/clousight-bench/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-docs.clousight.com-blue.svg)](https://docs.clousight.com)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/clousight/clousight-bench)

**[Clousight](https://clousight.com)'s reproducible benchmark harness for cloud products** —
it runs **recognized benchmark suites unmodified** (SWE-bench Verified first) against
**managed cloud SUTs** (agent runtimes today), and every number it publishes carries a
verifiable provenance chain: which suite, which pinned dataset revision, which evaluator,
which scaffold — folded into a content fingerprint you can diff.

> Clousight Bench never re-scores a suite and never blends scores. The suite's own
> harness produces the verdict; we add the cloud-product dimension (runtime behavior,
> latency, trajectory, cost) and the reproducibility bookkeeping around it.

> **0.5.0 Developer Preview.** The whole pipeline runs locally with no cloud account
> (`mode: mock`). The Aliyun AgentRun adapter is `experimental` and live-validated
> (`cn-hangzhou` real-cloud campaigns); the docker-capable ECS driver host, the
> AgentRun SUT agent (oracle/llm modes) and the SWE-bench Verified suite are code
> complete — the first live SWE-bench smoke is gated only on account preconditions
> (see the [live runbook](docs/swe-bench-live-runbook.mdx)). Other clouds are skeletons.

**Repository status.** This repository is public and Apache-2.0 licensed.
`main` is protected: every change lands through a pull request that passes
ruff, pytest and the no-cloud smoke on Python 3.10–3.13 plus a separate
installed-wheel smoke. No approving review is required, force pushes and branch
deletion are blocked, and the rules bind administrators too. Commercial plugins
are developed in a separate private repository and are not required to run
anything in this one.

## Quick start (no cloud account, no docker)

```bash
git clone https://github.com/clousight/clousight-bench.git && cd clousight-bench
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# what is installed? (registered suites, evaluators, adapters)
.venv/bin/csbench list

# run SWE-bench Verified through the full stage machine in mock mode:
cat > mock.yaml <<'EOF'
target:
  mode: mock
EOF
.venv/bin/csbench run --domain agent-runtime --benchmark swe-bench \
    --platform local-sim --config mock.yaml

# open the web viewer: record list, provenance, trajectory waterfall (EN | 中文)
.venv/bin/csbench serve
```

The mock run exercises the real code path — RESOLVE → PREFLIGHT → SETUP → EXECUTE →
SCORE → PERSIST — over the suite's bundled fixture artifacts, and persists a schema-0.4
record with `swe-bench.resolved` and a full provenance block. `csbench serve` renders
it at `http://127.0.0.1:8787` (React UI, bilingual, dark/light, strict CSP, read-only).

One run is not a measurement — repeat and pool:

```bash
csbench run --domain agent-runtime --benchmark swe-bench --platform local-sim \
    --config mock.yaml --repeat 5 --warmup 1
```

Only runs sharing a `benchmark` **and** `environment` fingerprint are ever pooled.

## The reproducibility contract (read this first)

Every schema-0.4 record is attributable on independent axes, so you can tell whether
two numbers are even comparable:

| Field | Answers |
|---|---|
| `provenance` | *which suite* — suite id + pinned dataset revision, evaluator id, `unmodified` flag, scaffold |
| `fingerprints.benchmark` | *what* was measured — task, suite revision, dataset digest, controlled params |
| `fingerprints.environment` | *where* — region, mode (`mock`/`real`), environment facts |
| `fingerprints.implementation` | *which code* — core, domain pack, adapter, installed plugins |
| `fingerprints.record_digest` | the content digest of the record itself |

Each measurement carries `value`, `unit`, a `reproducibility_class`
(`deterministic` / `environmental` / `judge-based`) and an `official` flag —
official measurements are the upstream suite's own verdict under the suite's
namespace (`swe-bench.resolved`); custom evaluators are structurally confined to
their own namespace (`csbench conformance --suite` enforces it). A run ends in
exactly one `status`: `completed`, `failed`, `invalid` or `unsupported` — there is
no boolean `ok`, because "the platform does not support this" and "the run crashed"
are different results. We publish **per-dimension results, never a single blended
score**.

## Architecture

```
BenchmarkSuite.resolve → prepare → run   (the suite's OWN upstream harness, unmodified)
        │                        │
        │                 SUT invocation (cloud agent runtime; real trajectory + tokens)
        ▼                        ▼
Evaluator.evaluate(RawArtifacts) — pure, offline, namespaced Measurements
        ▼
schema-0.4 record  →  csbench serve (web viewer)  /  csbench query (SQL)
```

The core only orchestrates the lifecycle. Everything product- or suite-specific is a plugin:

| Plugin | One per | Examples |
|---|---|---|
| **BenchmarkSuite** | recognized suite | `swe-bench` (SWE-bench Verified, pinned HF revision) |
| **Evaluator** | scoring view | `official-swe-evaluator` (pure passthrough of the upstream verdict) |
| **DomainPack** | product category | `agent-runtime` (the cloud-infra shell: adapters, probes, reaper) |
| **ProviderAdapter** | (domain, cloud) | `local-sim`, `aliyun-agentrun`, `aws-agentcore`, … |
| **WorkloadEngine** | load generator | any language: `manifest.yaml` + executable + JSONL on stdout |

All of them register via entry points (`clousight_bench.benchmark_suites`,
`.evaluators`, `.domains`, `.runtime_providers`) — third-party packs install like any
Python package and appear in `csbench list`.

| Adapter | Status | Runnable |
|---|---|---|
| `local-sim` | reference | yes |
| `aliyun-agentrun` | experimental | preview (live-validated) |
| `aws-agentcore` | skeleton (provider in-tree) | mock |
| `huawei-agentarts` | skeleton | mock |
| `volcengine-agentkit` | skeleton | mock |

`skeleton` clouds run end-to-end in `mode: mock` with no account and become live-runnable
when a **runtime provider** registers via `clousight_bench.runtime_providers`.

## Benchmarking a real platform

Credentials are **never** stored in configs — the cloud's own default credential
chain (env vars / CLI profile / attached role) is reused. The real-cloud SWE-bench
path runs on a **docker-capable ECS driver host** provisioned by `csbench submit`
(OSS-only control plane, self-destructing controller, terraform backstop):

```bash
csbench init aliyun                 # scaffold a private config (auto-gitignored)
csbench doctor --config agent-runtime-aliyun.local.yaml
csbench submit configs/swe-bench-smoke.plan.yaml --config agent-runtime-aliyun.local.yaml
csbench status <campaign-id> --config ...   # then: logs / fetch / teardown
```

The step-by-step live runbook — preconditions, cn-region gotchas (docker registry
mirror, HF mirror), the live-verification checklist and expected outcomes — is
[docs/swe-bench-live-runbook.mdx](docs/swe-bench-live-runbook.mdx) (EN | 中文).
Adapters surface the runtime's own behavior and must **never** touch suites or
scoring. You pay your own cloud bill; you get numbers for your own account,
network and region. That is the point.

## Analysis & viewing

```bash
csbench serve                 # web viewer: records, provenance, trajectory waterfall
csbench query "SELECT platform, avg(value_num) FROM measurements WHERE name='swe-bench.resolved' GROUP BY platform"
csbench export measurements --out m.parquet   # optional [store] extra: Parquet + DuckDB
```

Cost is presented as **list → discount → net** (public price feed via
`CLOUSIGHT_PRICING_DATA`, private discounts via `CLOUSIGHT_PRICING_DISCOUNTS`;
see [docs/querying.md](docs/querying.md)).

## Status

- [x] Core: lifecycle orchestrator, `RunSpec`/`ResultRecord` schema 0.4 with provenance-folded fingerprints, entry-point plugin registry, cross-language workload protocol, DuckDB-backed `csbench query`, cost budget + live-run gate + resource reaper (`csbench sweep`)
- [x] **Suite contract (Sub-project B)**: `BenchmarkSuite`/`Evaluator` ABCs, `suite:<id>` runs, SWE-bench Verified at a pinned HF revision with real gold-patch fixtures, official evaluator + namespace conformance, real SUT invocation on Aliyun AgentRun (oracle/llm agent modes) with real trajectory + token capture
- [x] **Driver host (Sub-project A)**: docker-capable ECS controller (`csbench submit`), suite-aware LaunchSpec, OSS-only control plane, self-destruct reaper
- [x] **Web viewer (Sub-project C)**: `csbench serve` — React UI (prebuilt, shipped in the wheel), record list/detail, transcript + ECharts waterfall trace views, EN | 中文, dark/light, strict CSP, offline-first
- [x] **OLAP suites (`data-warehouse` domain)**: TPC-DS **and** TPC-H on a `duckdb-local` reference platform. Both run offline (`suite:tpc-ds` / `suite:tpc-h`, mock + real DuckDB SF1); correctness vs a pinned SF1 reference, honest per-query latency (no audited QphDS/QphH). See [docs/tpcds-suite.mdx](docs/tpcds-suite.mdx)
- [x] **Key-value domain + config-connect abstraction**: **YCSB** on a `key-value` domain — the SUT-connection abstraction generalized so a suite runs against a local reference (`ycsb-local`, binding=basic) or an **already-running service via config** (`ycsb-endpoint`, binding+endpoint). Wraps the recognized upstream YCSB tool; offline mock path in CI, honest throughput + tail-latency (environmental). See [docs/ycsb-suite.mdx](docs/ycsb-suite.mdx)
- [x] **OLTP domain**: **TPC-C via BenchBase** on a `transactional-db` domain — `benchbase-local` (dbtype=sqlite reference) or `jdbc-endpoint` (config-connect to an already-running database). Wraps the recognized upstream BenchBase tool (Apache-2.0); offline mock path in CI, honest throughput/goodput/latency (environmental; audited tpmC not claimed). See [docs/tpcc-suite.mdx](docs/tpcc-suite.mdx). Data-systems coverage is now OLAP + KV + OLTP.
- [x] **LLM domain (test the managed model itself)**: **MMLU** on an `llm` domain — the SUT is a managed LLM endpoint (Bedrock/DashScope/Vertex/any OpenAI-compatible), config-connected via `llm-endpoint` (base_url + model + credentials) or the offline `llm-mock` reference. Runs recognized benchmarks unmodified (**MMLU** + **GSM8K**) → objective accuracy (deterministic) + serving dimensions latency/tokens/cost (environmental). See [docs/mmlu-suite.mdx](docs/mmlu-suite.mdx) / [docs/gsm8k-suite.mdx](docs/gsm8k-suite.mdx)
- [x] **pytest & CI gating**: any suite runs as a native pytest test (`assert_run` / the `clousight` fixture, auto-loaded via the `pytest11` entry point) or a CI exit-code gate (`csbench run --assert`), with min/max thresholds per measurement — so a benchmark becomes a red/green check in an enterprise CI pipeline. See [docs/pytest-ci.mdx](docs/pytest-ci.mdx)
- [ ] First **live** SWE-bench smoke on Aliyun (code complete; gated on account preconditions — see the runbook)
- [ ] Wire the remaining clouds (`huawei-agentarts` / `volcengine-agentkit` / `aws-agentcore` live paths); cloud-provisioned & existing-service backends for the data domains (managed KV/RDBMS, EMR/Spark, cloud DWH) at big-data scale
- [ ] More suites (τ-bench, Nexmark/streaming) + domain packs (streaming / graph / ml-systems)

## Contributing

Sign your commits (`git commit -s`, [DCO](https://developercertificate.org/)).
Adding a suite = one `BenchmarkSuite` + one `Evaluator` (the SWE-bench pilot in
`src/clousight_bench/suites/gsm8k/` is the simplest template; see docs/adding-a-suite); adding a platform = one adapter file + one
example config; adding a product category = one DomainPack. PRs that change suite
wiring or scoring for a shipped suite require a version bump and a changelog entry —
published numbers must stay attributable.

This checkout has no `origin` remote — commit/push/PR/merge go through `scripts/gitsync.sh` (requires the `clousight-dev` `gh` account and forces commit identity to that account's noreply email; `push` refuses `main` — land via a feature-branch PR with squash merge; run `cp .gitsync.env.example .gitsync.env` once to set the target repo).

## License

[Apache-2.0](LICENSE)
