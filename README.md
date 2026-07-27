# Clousight Bench · 指北测评

**云计算指北 / [Clousight](https://clousight.com) 出品的云产品可复现测评框架** — agent runtimes today; big data clusters, databases, compute and messaging via the same abstraction.

> Clousight Bench is the measuring stick of Clousight: open methods anyone can reproduce; evidence-graded results, never a blended vanity score.

> **0.2.0 Developer Preview.** The local reference baselines are runnable.
> Real-cloud adapters are visible for contributors but are not wired yet.

**Repository status.** This repository is public and Apache-2.0 licensed.
`main` is protected: every change lands through a pull request that passes
ruff, pytest and the no-cloud smoke on Python 3.10–3.13 plus a separate
installed-wheel smoke. No approving review is required, force pushes and branch
deletion are blocked, and the rules bind administrators too. Commercial plugins
are developed in a separate private repository and are not required to run
anything in this one.

Run `csbench list --verbose` to inspect task metadata and adapter readiness.

| Adapter | Status | Runnable |
|---|---|---|
| `local-sim` | reference | yes |
| `local-process` | reference | yes |
| `aliyun-agentrun` | skeleton | no |
| `huawei-agentarts` | skeleton | no |
| `volcengine-agentkit` | skeleton | no |
| `aws-emr` | skeleton | no |

Adapter status is part of the public contract:
`reference` and `wired` can run; `experimental` can run with preview caveats;
`skeleton` is discoverable for contributors but is rejected before preflight.

## The reproducibility contract (read this first)

Every number this framework produces is classified before you trust it:

- **Controlled-variable measurement (evidence layer C)** — the tested variable is controlled by our runner and mock services. **Precisely reproducible**: run the same code against your own account and challenge our numbers.
- **Environment observation (evidence layer B)** — cold starts, network-sensitive latency. The *method* is reproducible; the *numbers* depend on your network / hardware / region.
- **Documentation reading (evidence layer A)** — vendor-stated limits we did not measure.
- **Marketing material (evidence layer D)** — never used as load-bearing evidence.

Every result record is schema `0.2` and is attributable on three independent
axes, so you can tell whether two numbers are even comparable:

| Field | Answers |
|---|---|
| `fingerprints.benchmark` | *what* was measured — task, scorer, workload, assets, controlled params |
| `fingerprints.environment` | *where* — region, mode and the environment facts the task declares |
| `fingerprints.implementation` | *which code* — core, domain pack, adapter and installed plugins |
| `fingerprints.record_digest` | the content digest of the record itself |

Each measurement carries its own `value`, `unit` and `evidence` layer, and each
finding carries a stable `code`, a `severity` and its evidence. A run ends in
exactly one `status`: `completed`, `failed`, `invalid` or `unsupported` — there
is no boolean `ok`, because "the platform does not support this" and "the run
crashed" are different results. We publish **per-dimension results, never a
single blended score** — blended agent-benchmark rankings have near-zero
cross-benchmark agreement.

Results written by an older version use schema `1.0`. Convert them with:

```bash
csbench migrate-results old-results/ --output new-results/
```

The migrator never writes in place, never fabricates a fingerprint (unknown
ones are the literal string `unknown`), and produces byte-identical output when
run twice.

One run is not a measurement. Repeat a benchmark and get a distribution:

```bash
csbench run --domain agent-runtime --task T1.3 --platform local-sim \
  --repeat 5 --warmup 1
```

The warmup run is discarded; the five measured runs are reduced to `mean`,
`stdev`, `p95` and `cv` (numeric) or a value distribution (labels), and only
runs that share a `benchmark` and `environment` fingerprint are ever pooled.
`csbench report` flags any cell whose numbers are not actually comparable.

## Why another benchmark framework

Existing benchmarks pin the runtime and swap the model to report accuracy. Nobody independently benchmarks the **platform runtime engineering** — session hosting, tool-failure recovery, trace completeness, cost attribution — of managed cloud products. Clousight Bench does, and the abstraction generalizes: workloads differ wildly across cloud products, but the pipeline is identical:

```
provision -> setup -> execute -> collect -> teardown -> score -> report
```

The core only orchestrates that lifecycle. Everything product-specific is a plugin:

| Plugin | One per | Examples |
|---|---|---|
| **DomainPack** | product category | `agent-runtime`; `bigdata-emr` (available: `local-process` reference, `aws-emr` skeleton); database / compute / messaging (planned) |
| **ProviderAdapter** | (domain, cloud) | `local-sim`, `local-process`, `aliyun-agentrun`, `huawei-agentarts`, `volcengine-agentkit`, `aws-emr` |
| **WorkloadEngine** | load generator | any language, process boundary: `manifest.yaml` + executable + JSONL on stdout. Wrap YCSB / TPC-DS / OpenMessaging Benchmark / fio instead of reimplementing them. |

Domains register via the `clousight_bench.domains` entry point — third-party packs install like any Python package and appear in `csbench list`.

## Quick start (no cloud account needed)

```bash
git clone https://github.com/clousight/bench.git clousight-bench && cd clousight-bench
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# what is installed?
.venv/bin/csbench list

# T1.3 tool-failure recovery against the local simulated runtime:
# a deterministic fault hits the 3rd tool call; watch two runtime policies react
.venv/bin/csbench run --domain agent-runtime --task T1.3 --platform local-sim
.venv/bin/csbench run --domain agent-runtime --task T1.3 --platform local-sim \
    --config configs/local-sim.fail-fast.yaml

# J1.1 wordcount through the packaged local-process workload:
.venv/bin/csbench run --domain bigdata-emr --task J1.1 --platform local-process

# aggregate everything under results/ into a comparison report
.venv/bin/csbench report
```

可选时序存储（Parquet + DuckDB）：`pip install clousight-bench[store]`

测评集分发（内置 / 公开远程下载校验 / 私有授权）见 `examples/asset-manifests/`（`assets:` 三层模板 + 公开数据集样例）。

Expected: the default (auto-retry) run ends `recovery_mode=auto-retry, final_state=completed`; the fail-fast run ends `recovery_mode=fail-fast, final_state=aborted`. The mock tool universe is pinned and fault injection is counter-based (the Nth call fails, no randomness), so the run is replayable by construction.

## Benchmarking a real platform

Credentials are **never** stored in configs. Clousight Bench reuses the cloud's
own default credential chain — the same env vars / CLI profile / role you already
use for `aws`, `aliyun`, etc. — so you don't mint a benchmark-only secret.

```bash
# 1. scaffold a private config + .env.example (auto-gitignored, no secrets)
csbench init aws                 # or: aliyun / huawei / volcengine
#    -> agent-runtime-aws.local.yaml  +  .env.example

# 2. provide credentials via ANY of:
#      export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...   (or copy .env.example -> .env)
#      set target.profile in the config                          (a named CLI profile)
#      an attached role / SSO / instance metadata                (nothing to set)

# 3. expose the pinned mock tool universe where the cloud runtime can reach it
#    (localhost is NOT reachable from a cloud runtime — use a tunnel / cloud function)
python -m clousight_bench.domains.agent_runtime.mock_tools --port 8770
#    then set mock_base_url in the config to that public URL

# 4. preflight — checks provider, SDK, credential chain, and mock reachability
csbench doctor --config agent-runtime-aws.local.yaml

# 5. run
csbench run --domain agent-runtime --task T1.3 --platform aliyun-agentrun --config your.local.yaml
```

Adapters surface the runtime's own retry / session / trace behavior and must
**never** touch tasks or scoring. You pay your own cloud bill; you get numbers
for your own account, network and region. That is the point.

## Status

- [x] Core: lifecycle orchestrator, unified `RunSpec`/`ResultRecord` schema, entry-point plugin registry, cross-language workload protocol, markdown comparison report
- [x] Onboarding: `csbench init` (scaffold private config + `.env.example`, auto-gitignored) and `csbench doctor` (preflight credentials + connectivity); credentials reuse the cloud's default chain (env / profile / role), never stored in configs
- [x] `agent-runtime`: fault-injectable mock tool server, `local-sim` adapter, **five dimensions** end-to-end on `local-sim` — T1.2 state persistence · T1.3 tool-failure recovery · T2.1 tool registration paths (MCP/OpenAPI/native) · T4.1 trace completeness (OpenInference) · T4.2 OTel export
- [ ] `agent-runtime`: wire aliyun-agentrun / huawei-agentarts / volcengine-agentkit adapters (skeletons in-tree)
- [x] `bigdata-emr`: J1.1 wordcount smoke via the cross-language workload protocol and `local-process` reference adapter
- [ ] `bigdata-emr`: wire the `aws-emr` Terraform-backed adapter (skeleton in-tree)
- [ ] database / compute / messaging domain packs

## Contributing

Sign your commits (`git commit -s`, [DCO](https://developercertificate.org/)). Adding a platform = one adapter file + one example config; adding a dimension = one task file with its scoring and declared evidence layer; adding a product category = one DomainPack. PRs that change task or scoring logic for a shipped dimension require a version bump and a changelog entry — published numbers must stay attributable.

## License

[Apache-2.0](LICENSE)
