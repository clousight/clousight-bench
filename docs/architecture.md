# Architecture

Clousight Bench benchmarks the **runtime engineering of cloud products**, not
model intelligence. Its one idea: workloads differ wildly across cloud products,
but the pipeline is identical.

## Lifecycle (shared by every domain)

```
RESOLVE -> PREFLIGHT -> SETUP -> EXECUTE -> TEARDOWN -> RECORD
```

- **RESOLVE** — look up the DomainPack, Task and Adapter for a `RunSpec`.
- **PREFLIGHT** — `adapter.preflight()`: check credentials / permissions / connectivity **before provisioning**. A CRITICAL failure aborts here with an actionable checklist (no resource created), so prerequisites surface up front instead of mid-run. Bypass with `csbench run --skip-preflight`; run standalone with `csbench doctor`. Single source of truth: `core/preflight.py`.
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

- `agent-runtime` — sessions, tool calling, fault recovery, observability. Five dimensions implemented (all runnable on `local-sim`): T1.2 state persistence · T1.3 tool-failure recovery · T2.1 tool registration paths (MCP/OpenAPI/native) · T4.1 trace span completeness (OpenInference) · T4.2 OTel export compat. Capability probes raise `CapabilityNotSupported` → recorded as a finding, never a crash.
- `bigdata-emr` — skeleton proving the abstraction generalizes: J1.1 wordcount smoke via the cross-language workload protocol.

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

**插件兼容契约**：`clousight_bench.PLUGIN_API_VERSION = "1.0"`（SemVer）。当 schema 字段、entry-point group 名、`ResultEnricher`/`ResultStore` 签名发生不兼容变更时才升 MAJOR；商业插件的 `pyproject.toml` 应 pin `clousight-bench>=1.0,<2.0`。
