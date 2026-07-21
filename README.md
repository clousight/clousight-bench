# OpenCloudBench

**Reproducible, evidence-graded benchmarking for cloud products** — agent runtimes today; big data clusters, databases, compute and messaging via the same abstraction.

## The reproducibility contract (read this first)

Every number this framework produces is classified before you trust it:

- **Controlled-variable measurement (evidence layer C)** — the tested variable is controlled by our runner and mock services. **Precisely reproducible**: run the same code against your own account and challenge our numbers.
- **Environment observation (evidence layer B)** — cold starts, network-sensitive latency. The *method* is reproducible; the *numbers* depend on your network / hardware / region.
- **Documentation reading (evidence layer A)** — vendor-stated limits we did not measure.
- **Marketing material (evidence layer D)** — never used as load-bearing evidence.

Every result record carries `config_hash` + `runner_version` + `evidence_layer`, so you can tell exactly which configuration produced a number and how trustworthy it is. We publish **per-dimension results, never a single blended score** — blended agent-benchmark rankings have near-zero cross-benchmark agreement.

## Why another benchmark framework

Existing benchmarks pin the runtime and swap the model to report accuracy. Nobody independently benchmarks the **platform runtime engineering** — session hosting, tool-failure recovery, trace completeness, cost attribution — of managed cloud products. OpenCloudBench does, and the abstraction generalizes: workloads differ wildly across cloud products, but the pipeline is identical:

```
provision -> setup -> execute -> collect -> teardown -> score -> report
```

The core only orchestrates that lifecycle. Everything product-specific is a plugin:

| Plugin | One per | Examples |
|---|---|---|
| **DomainPack** | product category | `agent-runtime`, `bigdata-emr` (skeleton), database / compute / messaging (planned) |
| **ProviderAdapter** | (domain, cloud) | `local-sim`, `aliyun-agentrun`, `huawei-agentarts`, `volcengine-agentkit`, `aws-emr` |
| **WorkloadEngine** | load generator | any language, process boundary: `manifest.yaml` + executable + JSONL on stdout. Wrap YCSB / TPC-DS / OpenMessaging Benchmark / fio instead of reimplementing them. |

Domains register via the `opencloudbench.domains` entry point — third-party packs install like any Python package and appear in `ocb list`.

## Quick start (no cloud account needed)

```bash
git clone https://github.com/<org>/opencloudbench && cd opencloudbench
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# what is installed?
.venv/bin/ocb list

# T1.3 tool-failure recovery against the local simulated runtime:
# a deterministic fault hits the 3rd tool call; watch two runtime policies react
.venv/bin/ocb run --domain agent-runtime --task T1.3 --platform local-sim
.venv/bin/ocb run --domain agent-runtime --task T1.3 --platform local-sim \
    --config configs/local-sim.fail-fast.yaml

# aggregate everything under results/ into a comparison report
.venv/bin/ocb report
```

Expected: the default (auto-retry) run ends `recovery_mode=auto-retry, final_state=completed`; the fail-fast run ends `recovery_mode=fail-fast, final_state=aborted`. The mock tool universe is pinned and fault injection is counter-based (the Nth call fails, no randomness), so the run is replayable by construction.

## Benchmarking a real platform

1. Copy the example config: `configs/agent-runtime.aliyun.example.yaml` → fill your endpoint / region / env-var names (never put secrets in configs).
2. Expose the mock tool server where the cloud runtime can reach it (tunnel or a tiny cloud function): `python -m opencloudbench.domains.agent_runtime.mock_tools --port 8770`.
3. Implement / complete the adapter under `src/opencloudbench/domains/agent_runtime/adapters/` — adapters surface the runtime's own retry / session / trace behavior and must **never** touch tasks or scoring.
4. `ocb run --domain agent-runtime --task T1.3 --platform aliyun-agentrun --config your.yaml`

You pay your own cloud bill; you get numbers for your own account, network and region. That is the point.

## Status

- [x] Core: lifecycle orchestrator, unified `RunSpec`/`ResultRecord` schema, entry-point plugin registry, cross-language workload protocol, markdown comparison report
- [x] `agent-runtime`: fault-injectable mock tool server, `local-sim` adapter, **T1.3 tool-failure recovery** end-to-end
- [ ] `agent-runtime`: T1.2 state persistence, T2.1 tool registration paths, T4.1 trace completeness (OpenInference schema), T4.2 OTel export
- [ ] `agent-runtime`: wire aliyun-agentrun / huawei-agentarts / volcengine-agentkit adapters (skeletons in-tree)
- [x] `bigdata-emr` skeleton: J1.1 wordcount smoke via the cross-language workload protocol, `local-process` adapter, `aws-emr` Terraform-backed adapter skeleton
- [ ] database / compute / messaging domain packs

## Contributing

Sign your commits (`git commit -s`, [DCO](https://developercertificate.org/)). Adding a platform = one adapter file + one example config; adding a dimension = one task file with its scoring and declared evidence layer; adding a product category = one DomainPack. PRs that change task or scoring logic for a shipped dimension require a version bump and a changelog entry — published numbers must stay attributable.

## License

[Apache-2.0](LICENSE)
