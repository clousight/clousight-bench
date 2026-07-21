# Architecture

Clousight Bench benchmarks the **runtime engineering of cloud products**, not
model intelligence. Its one idea: workloads differ wildly across cloud products,
but the pipeline is identical.

## Lifecycle (shared by every domain)

```
RESOLVE -> SETUP -> EXECUTE -> TEARDOWN -> RECORD
```

- **RESOLVE** — look up the DomainPack, Task and Adapter for a `RunSpec`.
- **SETUP** — `adapter.setup()`: provision (Terraform) or connect (SDK/HTTP).
- **EXECUTE** — `task.run(adapter, params)`: the task drives the workload and scores it.
- **TEARDOWN** — `adapter.teardown()`: always runs, even on failure.
- **RECORD** — wrap into a `ResultRecord` (mandatory `config_hash` +
  `runner_version` + `evidence_layer`) and persist.

A failure is captured as an `ok=False` record, never a crash — "the platform
failed" is itself a finding.

## Layers

```
CLI (csbench)
  └─ Orchestrator (lifecycle state machine)
       ├─ Registry (entry-point domain discovery)
       ├─ DomainPack  → Task(s) + Adapter(s)         [per product category]
       │     └─ Adapter (setup/submit/teardown)      [per (domain, cloud)]
       │           └─ WorkloadEngine (JSONL, any language)  [per load generator]
       ├─ Schema (RunSpec / ResultRecord / config_hash / evidence layer)
       └─ Report (per-dimension matrix + red flags)
```

## Plugin contracts

| Contract | Responsibility | Loaded via |
|---|---|---|
| `DomainPack` | declare tasks + adapters for a product category | `clousight_bench.domains` entry point |
| `ProviderAdapter` | provision / talk to / tear down one system under test | referenced by a DomainPack |
| `Task` | one benchmark dimension: config (hashed), run, score, evidence layer | referenced by a DomainPack |
| `WorkloadEngine` | run a manifest-described load generator as a subprocess | `workloads/<name>/manifest.yaml` |

Built-in and third-party (including closed-source commercial) packs load
identically — installing a package or dropping in a workload directory is enough.

## Evidence layers

`A` docs · `B` environment observation · `C` controlled measurement · `D` marketing.
Reports never blend dimensions into one score.

## Current domains

- `agent-runtime` — sessions, tool calling, fault recovery, observability. T1.3 implemented; T1.2/T2.1/T4.1/T4.2 planned.
- `bigdata-emr` — skeleton proving the abstraction generalizes: J1.1 wordcount smoke via the cross-language workload protocol.
