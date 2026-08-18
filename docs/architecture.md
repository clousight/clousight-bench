# Architecture

Clousight Bench benchmarks the **runtime engineering of cloud products**, not
model intelligence. Its one idea: workloads differ wildly across cloud products,
but the pipeline is identical.

## Lifecycle (shared by every domain)

```
RESOLVE -> VALIDATE -> PREFLIGHT -> SETUP -> EXECUTE -> COLLECT
        -> SCORE -> ENRICH -> PERSIST -> optional PUBLISH
```

- **RESOLVE** — look up the DomainPack, Task and Adapter for a `RunSpec`.
- **VALIDATE** — parse and check the RunSpec, target, params and task config
  (`core/validation.py`). RESOLVE and VALIDATE failures are `UserInputError`s:
  CLI exit code 2, **no record written**.
- **PREFLIGHT** — `adapter.preflight(task)`: credentials, SDK, connectivity and
  the minimal permissions this benchmark needs on this cloud, **before**
  provisioning. A CRITICAL failure produces an `invalid` record and never
  enters SETUP. Bypass with `--skip-preflight`; run standalone with
  `csbench doctor`.
- **SETUP** — `adapter.setup()`: provision (Terraform) or connect (SDK/HTTP).
- **EXECUTE** — `task.execute(adapter, params)`: drive the workload, return an
  `ObservationBundle` of raw evidence only.
- **COLLECT** — `core/observation.py::collect()`: prove the bundle is well
  formed and canonically encodable.
- **SCORE** — `task.score(bundle)`: a pure function producing measurements and
  findings. A scorer failure keeps the observations and records a SCORE error.
- **ENRICH** — installed `ResultEnricher`s, each isolated: a failure becomes an
  ENRICH stage error and never changes `status`.
- **PERSIST** — `core/store.py`: temp file, flush, `fsync`, atomic rename, plus
  an emergency dump into the system temp directory (path printed) when the
  results directory cannot be written.
- **PUBLISH** — off unless a `ResultPublisher` is injected. Runs after PERSIST
  and writes an append-only receipt; it can never rewrite the core record.

**TEARDOWN is not a step in that line.** It is the mandatory `finally` boundary
around SETUP → COLLECT: once SETUP is entered, `adapter.teardown()` always runs,
including when `setup()` failed half-way, and a teardown failure is recorded as
its own stage error without overwriting the execute or collect error that
caused it.

## Result contract (schema 0.2)

Top-level fields are fixed: `schema_version`, `run`, `identity`, `environment`,
`fingerprints`, `measurements`, `findings`, `observations`, `series`,
`artifacts`, `extensions`, `errors`, `status`.

- `status` ∈ `completed` · `failed` · `invalid` · `unsupported`. There is no
  boolean `ok`, no top-level `metrics`, no top-level `evidence_layer` and no
  `config_hash`.
- `environment.execution` ∈ `simulated` · `live` · `unknown` marks whether the
  numbers came from a simulated runtime or a real cloud. It is folded into the
  environment fingerprint, so simulated and live results never pool together.
- Every measurement carries `value`, `unit` and `evidence`, optionally
  `aggregation`, `sample_count` and `notes`.
- Every finding carries a stable `code`, a `severity`, a `summary`, its
  `evidence` and `details`.
- Every stage error carries `stage`, `code`, `type`, `message` and `retryable`.
  Tracebacks are never stored in a record; `csbench run --debug` writes them to
  `<results>/debug/<run_id>.log`.
- All digests are full SHA-256 over the canonical JSON encoding
  (`core/canonical.py`): UTF-8, sorted keys, no insignificant whitespace,
  NaN/Infinity rejected.
- `extensions["core"]` is reserved for the core; a plugin writes under its own
  name (for example `extensions["cb-pricing"]`).

Old schema `1.0` files are converted with
`csbench migrate-results SOURCE --output DEST [--dry-run]`: never in place,
never fabricating a fingerprint (unknown ones are the literal `unknown`), and
byte-identical on a repeat run. Each entry in `migration-manifest.json` records
the original path and its SHA-256.

## Run plans and statistics (Phase 1C)

A single number is not a measurement. `csbench run --repeat N --warmup W`
(`core/runplan.py`) runs the same `RunSpec` `warmup + repeat` times through the
lifecycle above — every run is still its own digested `0.2` record — then:

- discards the `warmup` runs (cold-start / JIT / cache effects are not the
  steady state you publish), tagging each record's role under
  `extensions["core"]["run_plan"]` so the choice is auditable, never a fingerprint;
- reduces the measured runs to one distribution per measurement
  (`core/statistics.py`): numeric → `n / mean / stdev / min / max / p50 / p95 /
  cv`, label → distribution / `mode` / `agreement`;
- writes a `run_plan_aggregate` (with its own SHA-256 digest) under
  `results/aggregates/…`, which the report loader skips as a summary-of-records.

**Comparability is checked, not assumed.** Two runs are pooled only when their
`benchmark` *and* `environment` fingerprints match. A plan whose benchmark or
environment changes mid-flight aggregates only the largest self-consistent
group and says so; `csbench report` flags a cell that mixes benchmarks (not
comparable at all) or implementation fingerprints (comparable only with the
caveat that the code changed). Only `completed` / `unsupported` runs contribute
numbers — a `failed` run is counted, but it has no verdict to pool.

Phase 1C does **not** itself ship plugin API ranges, JSON Schema, a conformance
kit or workload sandboxing — those are Phase 1D, which has since shipped in the
open core (plugin API version ranges + conflict detection, JSON Schema for
`RunSpec` / `ResultRecord` / manifest, `csbench conformance`, and workload
sandbox layers 1+2; see `ROADMAP.md` / `CHANGELOG.md`). Phase 1C aggregates only
scalar `measurements`, not time series.

## Layers

```
CLI (csbench)
  └─ Orchestrator (lifecycle state machine)
       ├─ Registry (entry-point domain discovery)
       ├─ DomainPack  → Task(s) + Adapter(s)         [per product category]
       │     └─ Adapter (setup/submit/teardown)      [per (domain, cloud)]
       │           └─ WorkloadEngine (JSONL, any language)  [per load generator]
       ├─ Schema (RunSpec / ResultRecord 0.2 / fingerprints / evidence layer)
       └─ Report (per-dimension matrix + red flags)
```

## Plugin contracts

| Contract | Responsibility | Loaded via |
|---|---|---|
| `DomainPack` | declare tasks + adapters for a product category | `clousight_bench.domains` entry point |
| `ProviderAdapter` | provision / talk to / tear down one system under test | referenced by a DomainPack |
| `Task` | one benchmark dimension: `config()`, `execute()` (raw observation), `score()` (pure), `task_revision` / `scorer_revision` | referenced by a DomainPack |
| `WorkloadEngine` | run a manifest-described load generator as a subprocess | `src/clousight_bench/resources/workloads/<name>/manifest.yaml`, resolved via `core/resources.py::reference_workload_path()` |
| `RuntimeProvider` | supply a wired, SDK-backed transport for a `skeleton` cloud adapter in real mode | `clousight_bench.runtime_providers` entry point, resolved via `registry.get_runtime_provider(provider)` |
| `CampaignProbeHook` | provision / sync / reap a per-campaign in-region load probe (see "Control plane vs. data plane" below) | looked up by `campaign_probe_hook(provider)` off the registered runtime provider |
| `ResourceReaper` | find and delete harness-tagged orphaned cloud resources for `csbench sweep` | `clousight_bench.resource_reapers` entry point, resolved via `registry.load_resource_reapers()` |

Built-in and third-party (including closed-source commercial) packs load
identically — installing a package or dropping in a workload directory is enough.
A pack wiring a real cloud registers a `RuntimeProvider` (and, optionally, a
`CampaignProbeHook` and `ResourceReaper`). The open core defines these
abstractions **and now ships reference implementations** for them — the live
Aliyun AgentRun and AWS AgentCore `RuntimeProvider`s and `ResourceReaper`s are
registered in-tree (`pyproject.toml`), so a real-cloud path is reproducible from
the open core alone. The seam stays open for additional third-party / commercial
providers to register the same way.

## Control plane vs. data plane

The `execute` (touches the cloud) / `score` (pure) split is also the control
plane / data plane split. `AgentRuntimeAdapter` carries one extra seam,
`run_data_plane_probe(name, params) -> ObservationBundle`
(`domains/agent_runtime/adapters/base.py`): a latency-class "data-plane" task's
`execute()` collapses to `return adapter.run_data_plane_probe("<probe>", {...})`,
so the *adapter*, not the task, decides where the measurement runs. The base
adapter dispatches to an in-process packer registry
(`domains/agent_runtime/dataplane_dispatch.py`), which is exactly what
`local-sim` and any not-yet-wired cloud use. A wired cloud can instead override
the seam to send the whole measurement to a load probe running **inside the
target region**, keeping the operator's network and system proxy out of the
numbers, and return the same `ObservationBundle` for scoring. `score()` is
unchanged either way. The probe lifecycle for a `run-plan` campaign is driven by
the optional `CampaignProbeHook` (started, synced and reaped in a `try/finally`
around the task loop; default `--probe local` leaves this path untouched). The
open core defines the seam and the hook contract **and ships the reference
Aliyun ECI probe carrier and its cloud transport in-tree**; additional carriers
(other clouds, third-party or commercial) register the same way.

## Evidence layers

`A` docs · `B` environment observation · `C` controlled measurement · `D` marketing.
Reports never blend dimensions into one score.

## Current domains

- `agent-runtime` — sessions, tool calling, fault recovery, observability, cost, isolation. All tasks run on `local-sim`; the full task list and adapter statuses are **generated from the registry below** (never hand-maintained — see "Maintaining this document"). It spans provisioning, runtime behaviour, tool registration, observability, cost and tenant isolation. Adapters use a `managed`/`transport`/`mode` split so the same code drives the local simulated runtime or, once a runtime-provider plugin is installed, a real cloud; latency-class tasks route through the `run_data_plane_probe` seam (see "Control plane vs. data plane"); capability probes raise `CapabilityNotSupported` → recorded as a finding, never a crash.
- **Reliability group — platform-as-agent-host stance.** The unit under test is *the platform as a host for a fixed agent*, not the raw invoke API, so the reliability tasks measure whether the "platform + agent" combination recovers when a downstream tool genuinely fails or stalls. **T1.3 (fault recovery) / T1.10 (retry storm) / T1.12 (head-of-line)** inject faults/latency through the mock tool server's `/fault/config` + `/latency/config` (so they are *platform-visible* on the real agent→tool HTTP hop, authenticated with the mock token like every other call), give the benchmark agent a **pinned retry contract** (`AGENT_RETRY_POLICY` in `agent_bundle/lc_agent.py`: HTTP 5xx → retry twice, 200 ms backoff, 3 attempts total; 4xx / connection failures → no retry — part of the agent fingerprint, not a tunable knob), and **observe by reading the mock's own call counter** (correlation-id bucketed so concurrent requests never cross-count) rather than inferring from the client loop. Each yields a **three-state platform attribution** — e.g. T1.3 → `recovered` vs `platform_terminated` (invoke cut before the agent could recover) vs fail-fast; T1.10 → `storm_bounded_by` agent / platform / none. **T1.2 (state persistence) / T1.11 (concurrent writes)** are *honestly downgraded* to `unsupported` with an evidence-A finding: FC-based AgentRun has no native session state, so a stateful platform legitimately scores a positive result here — a truthful differentiating negative, not a fabricated pass.
- `bigdata-emr` — minimal domain pack proving the abstraction generalizes: J1.1 wordcount smoke via the cross-language workload protocol. (This is a small task/adapter surface, not the `skeleton` `AdapterStatus` value — its `local-process` adapter is `reference`; only its `aws-emr` adapter is `skeleton`.)

### Task & adapter inventory

<!-- BEGIN generated:task-inventory -->
<!-- Generated by scripts/gen_docs.py from the domain registry (same source as `csbench list --json`). Do not edit by hand; run `python scripts/gen_docs.py`. -->

### `agent-runtime` — 27 tasks

| Task | Title | Evidence |
|---|---|---|
| T0.1 | Provisioning (deploy) latency | B |
| T0.2 | Teardown cleanliness | C |
| T1.1 | Cold/warm start latency | B |
| T1.2 | Session state persistence | A |
| T1.3 | Tool-failure recovery | B |
| T1.4 | Sustained load & tail latency | B |
| T1.5 | Warm-pool retention | B |
| T1.6 | Soak availability | B |
| T1.7 | Rate limiting | B |
| T1.8 | Timeout & cancellation | B |
| T1.9 | Time-to-first-token (TTFT) | B |
| T1.10 | Retry storm | B |
| T1.11 | Concurrent state writes | A |
| T1.12 | Head-of-line blocking | B |
| T1.13 | Startup-convergence curve (instance reuse) | B |
| T1.14 | Idle-timeout config honor | B |
| T2.1 | Tool registration paths | B |
| T4.1 | Trace span completeness (OpenInference) | C |
| T4.2 | OTel export compatibility | B |
| T4.3 | Metrics & logs | B |
| T4.4 | Span propagation | B |
| T4.5 | Export latency | B |
| T5.1 | Cost attribution | B |
| T5.2 | Elasticity under concurrency | B |
| T5.3 | Idle / scale-to-zero cost | B |
| T5.4 | Concurrency ceiling | B |
| T6.1 | Tenant isolation | B |

**Adapters:** `aliyun-agentrun` experimental · `aws-agentcore` skeleton · `huawei-agentarts` skeleton · `local-sim` reference · `volcengine-agentkit` skeleton

### `bigdata-emr` — 1 task

| Task | Title | Evidence |
|---|---|---|
| J1.1 | Batch job smoke (wordcount) | C |

**Adapters:** `aws-emr` skeleton · `local-process` reference
<!-- END generated:task-inventory -->

## 0.2 Developer Preview readiness

- `reference` and `wired` adapters can execute.
- `experimental` adapters can execute with preview caveats.
- `skeleton` adapters are discoverable but rejected before preflight.
- Current runnable references are `local-sim` and `local-process`. The Aliyun
  AgentRun (and AWS AgentCore) `RuntimeProvider`s are registered in-tree. The
  `aliyun-agentrun` adapter is `experimental`: it runs end-to-end in `mode: mock`
  and its live path has run a full 27-task real-cloud campaign (`cn-hangzhou`,
  2026-08-15 — 25 `completed` + 2 honestly `unsupported`). It is not yet promoted
  to `wired` (that status is reserved for a stabilized, repeatedly-validated live
  path); AWS AgentCore and the other clouds remain `skeleton`.
- Bundled workloads live in `clousight_bench.resources.workloads` and are
  resolved with `core.resources.reference_workload_path()`, so wheel and
  editable installs use the same files.

Phase 1B ships ResultRecord schema `0.2` (see "Result contract" above); plugin
API `1.0` stays until its range-negotiation replacement in Phase 1D.

This repository is public and Apache-2.0 licensed; it contains the whole open
core, **including the reproducibility mechanisms** — the live Aliyun AgentRun
`RuntimeProvider`, ECI probe carrier, `ResourceReaper`, Terraform and the seed
`pricing` enricher. The moat is *data and service*, not withheld code. The
separate private `clousight-bench-pro` repository ships only:

- `cb-pricing` — a fuller / fresher private price feed, consumed by the open
  `pricing` enricher via the `CLOUSIGHT_PRICING_DATA` environment variable (not
  an entry point);
- `cb-dataservice` — token-gated private / held-out datasets, registered as a
  `PrivateAssetResolver` on the `clousight_bench.asset_resolvers` entry point,
  plus a managed object-store upload (placeholder);
- `cb-adapters-enterprise` — an empty placeholder for future managed-SaaS and
  private-cloud / 信创 adapters.

Nothing in the open core imports, requires or degrades without them.

## 凭证与上手（便捷层）

原则：**绝不让用户为 benchmark 单独造一套密钥**，复用云自己的默认凭证链。

- `core/credentials.py::resolve_credentials(target, platform)` 只**探测**凭证来源、从不读取/存储密钥值。解析顺序：`auth_env`（逃生口，显式指定 env 名）→ `profile`（CLI 命名档）→ 标准 env 变量 → 凭证文件。返回 `CredentialResolution(ok, source, identity_hint, remediation)`，`identity_hint` 只含变量名/档名，永不含密钥。
- `ProviderAdapter.resolve_credentials()` 委托给上面的解析器（跨域，`provider` 由 `target.provider` 或平台名前缀推断，如 `aliyun-agentrun`→`aliyun`）。真 adapter 运行时仍交给官方 SDK 的默认链。
- 各云凭证链登记在 `PROVIDER_CREDENTIALS`（aws / aliyun / huawei / volcengine：标准 env、profile env、凭证文件、SDK 模块、文档链接）。
- CLI：`csbench init <provider>` 生成私有 `*.local.yaml` + `.env.example` 并写 `.gitignore`（配置无密钥）；`csbench doctor --config x` 分步体检（provider → SDK 可导入？→ 凭证链可解析？→ `mock_base_url` 可达？localhost 直接判失败），每步给可操作补救提示。

**前置校验阶段（PREFLIGHT）**：上述校验不仅是独立命令，还是**生命周期的一个阶段**。`core/preflight.py` 提供 `Check` / `PreflightReport` 与可复用校验函数（`credential_check` / `sdk_check` / `mock_reachable_check`），`doctor` 与 orchestrator **共用同一批函数**（单一真源）。

- `ProviderAdapter.preflight() -> PreflightReport`：默认校验凭证 + provider SDK。跨域可用。
- `AgentRuntimeAdapter.preflight(task)`：`super()` 之上，对**真云平台**（provider 非空）追加 `mock_base_url` 可达校验 + `check_permissions(task)`。local-sim（provider 为空、自托管 mock）不加这两项，永远通过。
- orchestrator 在 RESOLVE 之后、SETUP 之前跑 `adapter.preflight(task)`；任一 CRITICAL 失败即**早退**（不 provision），落一条 `ok=False`、`metrics.preflight_ok=False`、`error="preflight failed: ..."`、`notes=` 清单的记录。`--skip-preflight` 关闭该 gate。

### 最小权限：(benchmark × 云) 矩阵映射

不同 benchmark 对不同云依赖的最小权限不同，因此权限校验**按 task 映射**，职责拆成两侧：

- **Task 声明 WHAT**：`Task.required_permissions: tuple[str, ...]` 列出**抽象能力令牌**（与云无关）。令牌定义在 `domains/agent_runtime/permissions.py`：`session:create` / `session:state` / `tool:invoke` / `tool:register` / `trace:read` / `trace:export`。当前五维声明：T1.2=`session:create,session:state`；T1.3=`session:create,tool:invoke`；T2.1=`tool:register`；T4.1=`session:create,tool:invoke,trace:read`；T4.2=`session:create,tool:invoke,trace:export`。
- **Adapter 说 HOW + 验**：每个真云 adapter 定义 `PERMISSION_MAP`（令牌 → 该云的具体最小 IAM/RAM 动作），`required_actions(task)` 把 task 的令牌解析为该云动作集，`_probe_permissions(actions)` 做真实鉴权（默认 `None`=未验证 → WARNING 并列出「本次 benchmark 在该云所需的最小动作」；接真 adapter 覆写为 STS GetCallerIdentity / RAM policy 模拟 / dry-run，返回 `(ok, missing)` → 缺失即 CRITICAL 阻断）。

于是「本次运行的最小权限」= adapter 对 task 令牌的映射；**加维度**只需在 Task 上声明令牌，**加云**只需在 Adapter 上补 `PERMISSION_MAP`，互不侵入。`csbench doctor --domain <d> --platform <p> --task <t>` 直接打印该 (benchmark×云) 的最小动作清单。

## 测评集分发（三层，一套解析机制）

benchmark 不只是代码，还有**资产**（数据集、语料、held-out 判分键）。方法开源、数据分层、判分键私有——用同一套解析器承载，开放/私有边界只是配置：

| 层 | `source` | 放哪 | 机制 |
|----|----------|------|------|
| **内置开源** | `bundled` | `clousight-bench`（相对 workload/task 目录） | 小型、license 干净、随仓库走；有 `sha256` 则校验 |
| **公开外置** | `remote` | 不 vendored；manifest 声明 `uri + sha256 + license` | 按需下载 → 校验哈希 → 缓存到 `~/.cache/clousight-bench/assets`；`license` 必填（可审计） |
| **私有商业** | `private` | `clousight-bench-pro` / 数据服务 | 由入口点 `clousight_bench.asset_resolvers` 的解析器（带 token 下载）处理；未装解析器 → 抛 `NeedLicense`（清晰提示，非崩溃） |

- **manifest `assets:` 段**（workload / task）：
  ```yaml
  assets:
    - name: tpcds-sf1
      source: remote            # bundled | remote | private
      uri: https://.../tpcds_sf1.tar.zst
      sha256: "…"
      license: TPC-EULA
      version: "1"
  ```
- **`core/assets.py`**：`AssetSpec` + `resolve_asset(spec, base_dir, cache_dir, private_resolver)`；`WorkloadEngine.resolve_assets()` 在 run 前解析全部资产，把 `{name: 本地路径}` 通过 `params["assets"]` 暴露给 workload。
- **可复现不泄密**：`describe()` 把资产 `identity()`（`name@version + sha256`）折进 config_hash——**只存指纹，不存内容**；判分键内容永不入 record。
- **判分键抗污染**：一个 task 的方法（怎么判）可开源，但其 held-out 键值可声明为 `source: private`——"怎么判"公开、"标准答案"私有，天然抗背题。
- **扩展点**：私有解析器实现 `PrivateAssetResolver`（`name` + `resolve(spec, cache_dir)`），经 `clousight_bench.asset_resolvers` 注册；开源核心不带任何实现。`clousight-bench-pro` 的 `cb-dataservice` 提供 `DataServiceAssetResolver`（`CLOUSIGHT_BENCH_TOKEN` 鉴权 + sha256 校验）。

## 数据契约与扩展点

`ResultRecord` 承载三条独立通道，互不覆盖，读者按需选取：

- `metrics`（标量判分）— 报告层聚合、比较用的数字。
- `series`（时序，`{name: [[t, value], ...]}`）— 高频采样，如逐秒延迟、GPU 利用率。
- `artifacts`（`{"kind", "path"/"uri", "media", "sha256"}` 指针）— OTel trace、日志包等大文件，record.json 里只存指纹，不存内容。

协议（WorkloadEngine 的 stdout JSONL）新增两类事件，与既有 `metric`/`log`/`result` 并存、互不影响：

```jsonl
{"type": "sample", "series": "latency_ms", "t": 1.0, "value": 87.2}
{"type": "artifact", "kind": "otel_trace", "path": "trace.json", "media": "application/json"}
{"type": "result", "ok": true}
```

`sample` 事件按 `series` 名累积进 `WorkloadResult.series`；`artifact` 事件的 `sha256` 由引擎按 `workload_dir/path` 的文件内容计算，workload 本身不用算哈希。

**落盘**：`record.json` 保持历史路径 `results/<domain>/<platform>/<task_id>-<run_id>.json` 不变（`core/store.py::ResultStore` 负责，`orchestrator._persist` 委托给它，签名不变）。装了可选 `[store]` extra（`duckdb` + `pyarrow`）且记录带 `series` 时，series 会外置为该次 run 目录下的 `series.parquet` 长表，`record.json` 里的 `series` 字段改写为指针 `{"$parquet": "<相对路径>"}`；未装 extra 时 series 原样内嵌在 `record.json`（小规模无损）。

Parquet 长表列（`cb-dataservice` / SaaS Web 端读取的稳定握手）：

```
run_id | domain | task_id | platform | config_hash | series | t | value | unit
```

其中 `unit` 列的取值约定：`ResultStore` 写 Parquet 时，对每个 series `<name>` 从 `metrics["<name>__unit"]` 读取其单位字符串（如 `latency_ms` 系列可在 `metrics` 里放 `"latency_ms__unit": "ms"`）；未提供则为空串 `""`。这是一个**隐式约定而非强制**——不写也能落盘，只是 `unit` 列为空。

`ResultStore.query_series(sql=None, glob="**/series.parquet")` 用 DuckDB 直接对整个 `results_dir` 下的 Parquet 文件跑 SQL；`glob` 路径以参数绑定传入 `read_parquet(?)`（不做字符串拼接）；缺 `[store]` extra 时抛 `ImportError`。

**扩展点**：`clousight_bench.enrichers` entry-point group 承载 `ResultEnricher` 子类——`name: str` + `enrich(self, record: ResultRecord) -> ResultRecord`。`orchestrator.execute(spec, results_dir=None, enrich=True)` 在构造完 `record`、`_persist` 之前，按 `registry.load_enrichers()` 返回的列表（按 `name` 排序，确定性执行顺序）依次调用。核心不带任何 enricher 实现（如成本预估）——商业插件通过 entry point 注入；CLI `csbench run --no-enrich` 可跳过整个链路。

**插件兼容契约**：`clousight_bench.PLUGIN_API_VERSION = "1.0"`（SemVer）。当 schema 字段、entry-point group 名、`ResultEnricher`/`ResultStore` 签名发生不兼容变更时才升 MAJOR。**包版本与插件 API 版本是两个独立维度**：`clousight_bench.__version__`（当前 `0.2.0`）随每次发布递增，`PLUGIN_API_VERSION` 只在插件面发生不兼容变更时才动，二者不同步演进。Phase 1A 的商业插件 `pyproject.toml` 按当前包版本 pin `clousight-bench>=0.2,<0.3`（`>=1.0,<2.0` 是早期设计文档里按 `PLUGIN_API_VERSION` 主版本设想的目标态写法，尚未随 0.2 开发者预览调整回真实包版本，此处更正）。

**Pro 三个扩展边界**（由 `clousight-bench-pro` 通过 entry point 注入；开源核心不携带任何实现）：

| 接口 | 职责 | Phase 1A 状态 |
|---|---|---|
| `ResultEnricher`（`core/plugin.py`） | 对已生成的 `ResultRecord` 追加派生指标（如成本估算） | 已实现：`clousight_bench.enrichers` entry point，`registry.load_enrichers()` 加载 |
| `PrivateAssetResolver`（`core/plugin.py`） | 解析私有/授权资产（数据集、held-out 判分键） | 已实现：`clousight_bench.asset_resolvers` entry point；`cb-dataservice` 的 `DataServiceAssetResolver` 已注册 |
| `ResultPublisher` | 可选的结果发布/签名/团队报告 | **保留/计划中的第三边界，Phase 1A 未实现**——仅见于设计文档（`docs/superpowers/specs/2026-07-25-open-source-core-hardening-design.md`），核心未定义该抽象，Pro 也未注册任何实现 |

## Maintaining this document

Some of this document is generated, and the rest is prose you keep current by
hand — in the *same change* that alters the behaviour.

- **Generated blocks** live between `<!-- BEGIN generated:… -->` /
  `<!-- END generated:… -->` markers (currently the task & adapter inventory).
  They are rendered from the domain registry — the same source as
  `csbench list --json` — by `scripts/gen_docs.py`. Never edit inside the
  markers by hand. After adding/renaming a task or changing an adapter status,
  run `python scripts/gen_docs.py` and commit the result. CI runs
  `python scripts/gen_docs.py --check` and `tests/test_docs_inventory.py`, so a
  stale block (a hand-typed "28 tasks" that no longer matches the registry)
  fails the build instead of shipping.
- **Prose sections** (everything outside the markers) are part of the
  definition of done for a behaviour change: when a task's measurement shape,
  an adapter's contract, or a lifecycle rule changes, update the paragraph that
  describes it in the same PR. A doc that drifts a release behind is worse than
  no doc — reviewers stop trusting it. Keep each edit small and scoped to what
  changed; the generated blocks absorb the mechanical facts so the prose only
  has to carry intent.
