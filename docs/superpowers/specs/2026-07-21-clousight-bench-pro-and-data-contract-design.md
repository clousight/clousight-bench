# Clousight Bench 数据契约 + clousight-bench-pro 骨架 — 设计 Spec

> 日期：2026-07-21 · 状态：已确认待实现
> 配套决策记录：`cloudNew/docs/clousight-bench-open-core-strategy.md`（§4 数据模型、§5 时序存储、§6 三仓拓扑）
> 涉及仓库：`clousight-bench`（开源核心）、`clousight-bench-pro`（新建私有多模块仓）

---

## 0. 目标与范围

把开源核心的「面向 SaaS 富采样」数据契约落地，并新建商业插件私有仓的多模块骨架，让第一期跑出的数据从第一天起就是 SaaS-ready 形状，同时把 open-core 边界固化成可运行的代码结构。

**本轮范围（已与用户确认）**：

| 编号 | 事项 | 仓库 |
|------|------|------|
| A | core 数据契约：schema 字段 + JSONL 协议事件 + `core/store.py` + `ResultEnricher` 扩展点 | clousight-bench |
| E | `clousight-bench-pro` uv workspace 多模块骨架 | clousight-bench-pro（新建） |
| F | `cb-pricing` 接真（专有定价 enricher） | clousight-bench-pro |
| G | `cb-samplers` 可运行骨架 | clousight-bench-pro |
| H | `cb-dataservice` 可运行骨架 | clousight-bench-pro |

**明确不做（需凭证，单独一轮）**：B（AWS AgentCore adapter）、C（阿里云 AgentRun 接真）、I（`cb-adapters-enterprise` 仅占位 NotWired）、T1.2/T2.1/T4.1/T4.2 四维。

**依赖顺序**：A 是地基（G/H 消费其协议与 Parquet 格式，F 消费其 enricher 扩展点）→ 再 E → 再 F/G/H。

---

## 1. 已确认的关键决策

| 决策点 | 选择 |
|--------|------|
| pro 仓工具链 | **uv workspace**（多包 monorepo，各包 pip 可装） |
| core 存储依赖 | **可选 extra `[store]`**（duckdb+pyarrow）；未装时 series 内联 `record.json`（小规模无损降级） |
| 新仓 git | **仅本地 git 仓 + 首次提交**，无 remote（与现有 clousight-bench 一致） |
| cb-pricing 接入 | **core 新增开源 `ResultEnricher` 扩展点（entry point），cb-pricing 作为闭源实现插入**（接口开源 / 实现闭源） |

---

## 2. A：core 数据契约（clousight-bench，开源）

### 2.1 版本锚点
- `src/clousight_bench/__init__.py` 新增 `PLUGIN_API_VERSION = "1.0"`（SemVer），与既有 `RUNNER_VERSION` 并列。作为插件兼容契约：core 改契约先升此版本，pro 各包 pin。

### 2.2 `core/schema.py`
- `ResultRecord` 追加三字段（均带默认值，向后兼容）：
  ```python
  schema_version: str = "1.0"
  series: dict[str, list] = field(default_factory=dict)   # {"latency_ms": [[t, v], ...]}
  artifacts: list[dict] = field(default_factory=list)     # [{"kind","uri","media","sha256"}]
  ```
- `from_dict` 改为**容忍未知键**（只取已知字段构造），保证前向兼容——平台升级 schema 后旧 reader 不炸。
- `EVIDENCE_LAYERS` 等既有约束不变。

### 2.3 `core/workload.py` 协议扩展（向后兼容）
- JSONL 新增两种事件解析：
  - `{"type":"sample","series":<name>,"t":<epoch>,"value":<num>}` → 累积进 `series[name].append([t, value])`。
  - `{"type":"artifact","kind":<str>,"path":<rel>,"media":<mime>}` → 引擎补算 `sha256`，收进 `artifacts`。
- `WorkloadResult` 追加 `series: dict[str,list]` 与 `artifacts: list[dict]`。
- 既有 `metric`/`log`/`result` 事件行为不变。

### 2.4 `core/store.py`（新文件）
- `ResultStore(results_dir)` 成为落盘层，**与现有 `results/<domain>/<platform>/<task>-<run_id>.json` 布局兼容**（不破坏 report.py 与现有测试）：
  - 恒定写 record.json 到既有路径 `results_dir/<domain>/<platform>/<task_id>-<run_id>.json`。
  - 若 `[store]` 可用（`import duckdb, pyarrow` 成功）且 record 有 series：series 外置为 per-run 子目录 `results_dir/<domain>/<platform>/<run_id>/series.parquet`（tidy 长表列：`run_id | domain | task_id | platform | config_hash | series | t | value | unit`）；record.json 的 `series` 字段替换为指针 `{"$parquet": "<domain>/<platform>/<run_id>/series.parquet"}`（相对 results_dir）；artifacts 落 `results_dir/<domain>/<platform>/<run_id>/artifacts/`。
  - 若 extra 不可用：series 内联留在 record.json（小规模无损）。
- `query_series(results_dir, sql=None, glob="**/series.parquet")`：用 DuckDB 跨 run 聚合；缺 extra 时抛清晰 `ImportError`（提示 `pip install clousight-bench[store]`）。
- 长表 schema 与目录布局是 pro（cb-dataservice）与 SaaS web 的稳定握手格式。
- orchestrator 的 `_persist` 改为委托 `ResultStore`。

### 2.5 `ResultEnricher` 开源扩展点
- `core/plugin.py`：新增抽象基类
  ```python
  class ResultEnricher(ABC):
      name: str
      @abstractmethod
      def enrich(self, record: ResultRecord) -> ResultRecord: ...
  ```
- `core/registry.py`：新增经 entry point `clousight_bench.enrichers` 发现/加载 enricher 的逻辑（与 domains 加载同构）。
- `core/orchestrator.py`：产出 `ResultRecord` 后，依次调用所有已注册 enricher（顺序 = 注册名排序，保证确定性）。**开源核心不自带任何 enricher 实现**（pro 提供 pricing）。
- CLI：加 `--no-enrich` 开关跳过富化（默认启用）。

### 2.6 `pyproject.toml`
- 新增：
  ```toml
  [project.optional-dependencies]
  store = ["duckdb>=1.0", "pyarrow>=16"]
  ```
- entry-point 组 `clousight_bench.enrichers` 由 pro 侧声明，core 侧不注册实现。

### 2.7 测试（core）
- `tests/test_schema.py`：新增字段默认值、`from_dict` 容忍未知键。
- `tests/test_workload_protocol.py`（新）：`sample`/`artifact` 事件解析与 sha256。
- `tests/test_store.py`（新）：Parquet 往返 + `query_series`，`pytest.importorskip("duckdb")`；另测无 extra 时内联降级路径。
- `tests/test_enricher.py`（新）：注册一个测试 enricher，验证 orchestrator 钩子按序执行。

### 2.8 文档
- 更新 `docs/architecture.md`：数据三通道、store 布局、enricher 扩展点。
- README 增补 `[store]` extra 安装说明。

---

## 3. E：clousight-bench-pro 仓（私有，uv workspace）

### 3.1 目录结构
```
clousight-bench-pro/
├── pyproject.toml          # uv workspace 根（[tool.uv.workspace] members = ["packages/*"]）
├── LICENSE                 # 专有（All Rights Reserved / Commercial），非 Apache
├── README.md               # 标注 proprietary + 依赖 core 契约 + 与开源仓边界
├── .gitignore
└── packages/
    ├── cb-pricing/
    ├── cb-samplers/
    ├── cb-dataservice/
    └── cb-adapters-enterprise/     # 占位骨架（NotWired），本轮不实现
```

### 3.2 每包约定
- 独立 `pyproject.toml`；`dependencies = ["clousight-bench>=1.0,<2.0"]`（pin `PLUGIN_API_VERSION` 主版本）。
- 通过 entry point 注入 core（如 `clousight_bench.enrichers`）。
- 开发期用 uv workspace 让 pro 各包引用本地 `clousight-bench`（editable）。
- 本地 git 仓 + 首次提交，无 remote。

### 3.3 open-core 纪律
- pro 仓 LICENSE 为专有；**不含任何来自开源仓的复制实现**；只依赖 core 的公开接口/schema。
- core 侧不出现任何 pro 包名、import 或引用。

---

## 4. F：cb-pricing（接真，商业）

- `cb_pricing` 实现 `ResultEnricher`，entry point 注册到 `clousight_bench.enrichers`。
- **专有定价数据集** `cb_pricing/data/pricing.json`：按 `provider / service / unit / region` 的真实价目条目，**与开源 mock `agent_runtime/data/prices.json` 完全分离**（后者是给 mock 工具服务器的假数据，永远开源）。
- `PricingEnricher.enrich(record)`：
  - 从 `record.metrics` 读资源用量键（如 `vcpu_hours`、`tokens_1k`、`gb_month`）。
  - 匹配 `pricing.json` 单价 → 计算 `metrics["cost_usd"]`。
  - 写 `raw["pricing_breakdown"]`（逐项：unit、qty、unit_price、subtotal、region、price_source）。
  - 数据缺失时不臆造：标 `notes` 说明未覆盖项，`cost_usd` 只累加可计算部分。
- 测试：构造带资源用量的样例 record → 断言 `cost_usd` 与 breakdown。

---

## 5. G/H：可运行骨架

### 5.1 G cb-samplers
- `cb_samplers.HighFreqSampler`：包裹一个采样回调，按频率产出 `{"type":"sample","series":...,"t":...,"value":...}` JSONL。
- 附一个示例 workload 目录（manifest + entrypoint），用 sampler 产合成采样。
- **可运行判据**：本地跑该 workload → 经 core store 落出 `series.parquet`（装了 `[store]`）。
- 真云采样（GPU 利用率、token 级成本、冷启动分解）留清晰接口，本轮不接。

### 5.2 H cb-dataservice
- `cb_dataservice.rollup(run_dir)`：读 `series.parquet`，时间桶聚合（桶均值 + p99 + max，或 LTTB 降采样）生成 `series_rollup.parquet`。
- CLI：`cb-dataservice rollup <run_dir>`。
- **可运行判据**：对一个含 series.parquet 的 run 目录跑 rollup → 产出 rollup 文件且行数显著少于原始。
- R2/对象存储 + Postgres 上传留 stub（定义接口签名 + NotImplemented 说明），本轮不接。

---

## 6. 横切与验收

### 6.1 兼容契约
- `PLUGIN_API_VERSION`（core）+ Parquet 长表 schema = pro/web 握手；改契约先升版本 + changelog。

### 6.2 验收标准（DoD）
- [ ] core 全部现有测试 + 新增测试通过（`csbench` 跑 local-sim 基线不回归）。
- [ ] 装 `clousight-bench[store]` 后，一次 local run 产出 `record.json` + `series.parquet` + 可 `query_series`。
- [ ] 不装 store extra 时，run 仍成功，series 内联 record.json。
- [ ] `clousight-bench-pro` uv workspace 可 `uv sync`；四包结构就位。
- [ ] 装 cb-pricing 后，orchestrator 自动富化出 `cost_usd`（enricher 钩子生效）。
- [ ] cb-samplers 示例 workload 本地跑出 series；cb-dataservice rollup 本地跑通。
- [ ] core 仓无任何 pro 引用；pro 仓 LICENSE 专有。

### 6.3 收尾
- 更新 `cloudNew/docs/clousight-bench-open-core-strategy.md` §10：勾掉「core 落地 §4/§5」「新建 pro 仓」两项。
- 更新 core `docs/architecture.md`。

---

## 7. 风险与取舍

- **pyarrow 体积**：故用 optional extra 隔离，核心 `pip install clousight-bench` 仍仅 pyyaml。
- **enricher 顺序不确定性**：按注册名排序固定，避免多 enricher 结果漂移。
- **series 内联膨胀**：无 store extra 时仅适合小规模；文档明示大规模需装 extra。
- **pro 与 core 版本漂移**：SemVer pin + 数据格式即边界双保险。
