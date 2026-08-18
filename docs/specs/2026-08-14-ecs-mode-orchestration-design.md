# ECS-Mode Orchestration (prod profile) — Design

**Status:** Design approved (brainstorming), pending implementation plan.
**Date:** 2026-08-14

## Goal

Move the benchmark orchestrator off the laptop and onto an ephemeral in-region
ECS **controller** for real evaluation runs, so the laptop is reduced to a thin
submit/ops client and can go offline while the evaluation runs autonomously in
the cloud. This makes real-data runs reproducible (in-region vantage,
standardized carrier env) and removes the laptop as a required long-lived
dependency.

## Naming — run mode by INTENT, not implementation

A single config, two profiles named by intent (never "local"/"ecs" — those are
implementation details that cause "do I want local or ecs?" confusion):

- **`dev` (本地开发)** — laptop orchestration + local probe. For probe-logic
  debugging, functional smoke, mechanism exploration ONLY. Never produces
  reported/publishable numbers.
- **`prod` (生产测评)** — the ECS-resident package described here (controller +
  in-region carrier + OSS channel + self-teardown). The ONLY profile allowed to
  produce real data.

A profile is a **bundle** (orchestration location + probe carrier + cleanup
policy), not just a probe-carrier flag.

## Architecture overview

`prod` = one ephemeral in-region ECS controller that does BOTH orchestration and
probing (二合一). The laptop is a thin CLI. OSS is the sole comms channel (pure
polling — no long connection, so the laptop can power off).

```
Laptop CLI (thin)            OSS (control/data channel)      Controller ECS (in-region, ephemeral)
─────────────────           ──────────────────────────      ─────────────────────────────────────
submit ─┬ terraform apply (MAIN account · once) ─────────▶  bring up controller + NAT + network
        └ write launch spec ──▶ /control/launch/
                                              poll ◀──────── read launch spec
                                                             run the run-plan loop (SERIAL):
                                                               for task in task-set:
                                                                 provision runtime
                                                                 → probe (from controller, in-region)
                                                                 → deprovision
                                              ◀── write ───── manifest / logs / results / heartbeat
 [laptop may power off ✈]
status ◀─ read /campaigns/<id>.json ◀──────────────────────  (heartbeat + progress)
logs   ◀─ read /logs/<id>/
fetch  ◀─ download /results/<id>/ (JSON + series.parquet)
                                              done/timeout ─▶ restricted-role self-destruct:
                                                              tear down runtime + NAT + self
teardown ─ MAIN account terraform destroy (backstop, if controller failed to self-destruct)
```

Core idea: the controller is "the local run-plan main loop, lifted into the
cloud." The per-task logic (provision / probe / deprovision) is reused verbatim;
only the execution *location* moves to an in-region ECS instance, and the trigger
becomes an OSS launch spec with results written back to OSS.

## Components

### New

1. **`cb-controller` entrypoint** — runs on the controller ECS. Claims the
   campaign, polls the launch spec, drives the whole run-plan orchestration loop
   (internally reusing `core/orchestrator` per-task pipeline), writes campaign
   progress/logs/results/heartbeat to OSS, and self-destructs on completion or
   timeout.
2. **Terraform template extension** — a controller ECS resource + a restricted
   delete role, reusing the existing NAT/VPC/vswitch/security-group definitions.
3. **OSS campaign channel** — campaign-level objects (launch spec, campaign
   manifest, log stream, heartbeat, results) layered on top of the existing
   `OssChannel` job-level primitives.
4. **Thin local commands** — `submit`, `status`, `logs`, `fetch`, `teardown`.

### Reused

- `OssChannel` (`probe/oss_channel.py`) — job/progress/result/claim/stop/ready
  primitives.
- `EcsProbeCarrier` (`ecs_carrier.py`) — cloud-init/user-data build that installs
  the package and starts a service (start command changes `cb-probe` →
  `cb-controller`; extras change `[probe]` → `[probe,store]`).
- `core/orchestrator.py` — per-task provision/setup/execute/deprovision pipeline
  (timeout, interrupt-safety, cost budget, resource ledger) — already tested.
- `core/store.py` `ResultStore` — JSON + parquet sidecar result writing.
- `ResourceLedger` / `ResourceReaper` (`core/resource_ledger.py`, `plugin.py`).
- Existing terraform NAT/VPC/vswitch/security-group.

## OSS channel object layout

Everything lives under `<control_prefix>/<campaign_id>/`, reusing `OssChannel`'s
campaign layout and adding campaign-level objects:

```
oss://<bucket>/csbench-control/<campaign_id>/
  launch.json                   ← laptop submit writes: {tasks:[...], params, target, watchdog_timeout_s}
  claimed                       ← controller claim marker (idempotency; OssChannel.claim)
  heartbeat.json                ← controller writes every ~15s: {ts, current_task, phase}
  ledger.json                   ← controller writes after every provision/deprovision (ResourceLedger snapshot)
  status/manifest.json          ← controller writes campaign progress (pending→running→done per task)
  logs/controller.log           ← controller appends orchestration log (rolling sharded objects)
  results/<task_id>.json        ← per-task result (contains $parquet pointer when series present)
  results/<task_id>.series.parquet  ← per-task time-series sidecar (pyarrow)
  DONE | FAILED                 ← controller terminal marker
  stop                          ← laptop teardown writes stop signal (controller polls → stop + self-destruct)
```

## Data flow

1. **submit** — laptop (1) writes `launch.json`, (2) `terraform apply` (MAIN
   account) brings up controller + NAT.
2. **controller boot** — cloud-init installs `clousight-bench[probe,store]`,
   runs `cb-controller`, which `claim`s the campaign and polls `launch.json`.
3. **orchestration loop (serial)** — read launch → init manifest → `for task`:
   provision runtime → probe (from controller, in-region) → deprovision → write
   `results/<task>.json` + `series.parquet` → update `manifest` + refresh
   `heartbeat` + sync `ledger.json`.
4. **laptop ops (any time, after coming back online)** — `status` = read
   manifest + heartbeat; `logs` = read logs/; `fetch` = download results/ (JSON +
   parquet).
5. **wrap-up** — controller writes `DONE` → restricted-role self-destruct.

Key properties: fully poll-based (laptop can power off after writing launch);
per-task results land in OSS immediately (fetch retrieves completed work even if
the run later dies); heartbeat serves both the controller's own watchdog and the
laptop's liveness check.

## Time-series storage (parquet + duckdb)

Aligns with the existing store pipeline (single source of truth = JSON; series
externalized to parquet; duckdb reads parquet for analysis):

- `ResultStore._build_series_sidecar` (`core/store.py:333`) writes each record's
  `series` to `series.parquet` (pyarrow) and leaves a `{"$parquet": relpath,
  sha256, rows}` pointer in the JSON.
- `ResultStore.query_series` (`core/store.py:369`), `core/analytics.py`,
  `core/rollup.py`, and the `rollup`/`export` CLI commands read parquet via
  `duckdb.connect() + read_parquet`.
- `[store]` extra = `duckdb>=1.0, pyarrow>=16`; `STORE_AVAILABLE` gate.

Implications for prod: the controller's cloud-init installs the `[store]` extra
(so it can write parquet sidecars in-cloud); `fetch` pulls JSON + parquet
together; local `query`/`rollup`/`analytics` read the pulled parquet with duckdb
exactly as today. Time-series thus flows through the same parquet+duckdb path in
prod as locally.

## Resource safety — three-layer defense

Any layer failing is caught by the next.

| Layer | Who | Cleans | Trigger |
|---|---|---|---|
| Normal path | controller loop | deprovision runtime per task | each task completes (orchestrator, existing) |
| **Self-destruct watchdog** | controller itself | ALL runtimes (ledger-reverse-lookup) + NAT/EIP/SNAT + own ECS | done / timeout / stop |
| **Local backstop** | `teardown` command | MAIN account `terraform destroy` (idempotent) + ledger-reverse-lookup residuals | when controller failed to self-destruct |

### Controller self-destruct watchdog

- **Heartbeat** every ~15s: `heartbeat.json{ts, current_task}`.
- **Triggers:** (a) campaign complete → writes `DONE`; (b) total wall-clock
  exceeds `watchdog_timeout_s` (from launch); (c) polls a laptop-written `stop`.
- **Action:** using the **restricted delete role**, reverse-look-up every
  created resource in `ResourceLedger` → delete runtimes → tear down
  NAT/EIP/SNAT → finally `DeleteInstance` on itself. All at sub-account /
  restricted scope; never touches the MAIN account.

### ResourceLedger synced to OSS (key invariant)

The controller writes the ledger back to OSS (`ledger.json`) after every
provision/deprovision. So even if the controller instance dies entirely, the
local `teardown` can **pull the ledger from OSS** and delete residual runtimes
one by one — cleanup never depends on the controller (or the laptop) being alive.
This is what makes "laptop offline, no leak" hold.

### Error handling

- **A failed task does not abort the campaign** — mark FAILED in the manifest,
  continue to the next (orchestrator `record_failure` / interrupt-safety).
- **Results land in OSS immediately** — a mid-run crash still leaves completed
  tasks' JSON+parquet fetchable.
- **State drift note:** the controller uses SDK deletes on terraform-managed
  NAT/self, so the local terraform state drifts; `teardown`'s `terraform
  destroy` is idempotent (refresh finds already-deleted resources and skips) and
  doubles as state reconciliation.

### Liveness check

Local `status` reads heartbeat; if `ts` exceeds a threshold (e.g. 2× heartbeat
interval) it warns "controller may be dead, run teardown."

## NAT & lifecycle

- **Create** = laptop `terraform apply` (MAIN account) — brings up controller
  ECS + NAT + network + restricted role in one shot. MAIN account used exactly
  once, at submit time (seconds); laptop may then go offline.
- **Tear down** = controller restricted-role self-destruct (SDK, no MAIN
  account) + local `teardown` backstop (`terraform destroy`, idempotent).

## Orchestration config granularity (YAGNI)

`submit` sets the whole orchestration at launch: **task-set + global params**
(fast-dial values, per-task timeout, cost budget) + `watchdog_timeout_s`. Runs
**serial** (self-consistent with the single二合一 controller — a single instance
parallelizing probes would self-contend, exactly the local-carrier bottleneck).
Grouped-parallel execution is explicitly out of scope for v1 (would require
multiple carriers, conflicting with 二合一).

## Testing strategy

Philosophy: ~90% of logic verified with mocks (zero cost); real cloud only for
one small end-to-end campaign + one fault injection.

### No-cloud (unit, zero cost)

| What | How |
|---|---|
| OSS campaign channel | mock OssClient (in-memory/tmp dir): launch read/write, manifest, heartbeat, stop, results |
| Controller loop | run the loop against the local-sim (mock) adapter: read launch → serial tasks → manifest/results/DONE |
| Self-destruct watchdog | fake delete client + fake ledger; assert triggers (done/timeout/stop) → reverse-lookup → delete ORDER (runtime→NAT→self) |
| Ledger→OSS sync | assert ledger lands in OSS after each provision/deprovision; simulate controller death → reverse-lookup from OSS returns all residuals |
| 5 thin commands | mock OSS + fake terraform; assert submit/status/logs/fetch/teardown behavior |
| Terraform template | `terraform validate` + `plan` (no apply) |
| cloud-init user-data | build unit test (reuse EcsProbeCarrier user-data tests): asserts `[probe,store]` install + cb-controller start |

### Real-cloud smoke (last, minimal cost)

- **End-to-end small campaign** — submit a 1–2 light-task campaign (e.g. T1.13
  ~27s / T2.1) through the full prod flow: terraform apply → controller runs →
  results+parquet to OSS → local fetch → self-destruct. Verifies the path works;
  NAT uptime short; a few CNY.
- **Fault injection** — kill the controller deliberately; verify local
  `teardown` backstop + reverse-lookup ledger from OSS clears residuals (the
  leak-prevention path must be verified live once).

### Reuse dividend

The controller loop *is* `core/orchestrator` (already covered by tests); the new
code is just the "OSS-launch driver + results-to-OSS + self-destruct" shell.
Tests concentrate on that shell + the three-layer defense, not re-testing the
underlying orchestration.

## Settled decisions

- Naming by intent: `mode: dev` (本地开发) / `prod` (生产测评).
- prod controller = 二合一 (orchestration + probe on one in-region ECS).
- Ephemeral per-run controller; self-destructs after the batch.
- Pure OSS polling comms (laptop can power off).
- Fault cleanup = controller heartbeat + timeout self-destruct AND local
  `teardown` backstop.
- Orchestration config = task-set + global params, serial.
- NAT: MAIN-account terraform one-shot create; restricted-role self-destruct +
  local teardown backstop.
- Time-series via existing parquet sidecar + duckdb; controller installs
  `[store]`.

## Scope / non-goals (v1)

- Serial only. Grouped-parallel is out (conflicts with 二合一).
- No standing controller — ephemeral per-run.
- No network rebuild — reuse existing NAT (mock stays public FC; VPC-internal
  mock to eliminate NAT is a possible later optimization, not v1).
- `dev` profile unchanged (existing local run-plan path).

## Reuse map (file pointers)

- `probe/oss_channel.py` — OssChannel primitives (job/progress/result/claim/stop).
- `ecs_carrier.py` — EcsProbeCarrier, cloud-init user-data build.
- `core/orchestrator.py` — per-task pipeline.
- `core/store.py` — ResultStore, series parquet sidecar, query_series (duckdb).
- `core/analytics.py`, `core/rollup.py` — duckdb parquet analysis/rollup.
- `core/resource_ledger.py`, `plugin.py` (ResourceReaper) — resource ledger/reaper.
- `cli.py` — subcommand registration; where submit/status/logs/fetch/teardown slot in.
- `infra/terraform/aliyun-iam/` — NAT/VPC/vswitch/sg + `enable_nat`; extend with controller ECS + restricted role.
