# Phase 1C 运行计划与统计设计

## 1. 背景与边界

Phase 1B 交付了可信结果契约（ResultRecord `0.2`：`execute`/`score` 分离、三条
指纹、四态 `status`、确定性摘要、非破坏迁移）。Phase 1B 的显式非目标即为
Phase 1C：**运行计划（warmup / repeat）、跨重复的统计聚合、以及基于指纹的
可比性报告**。

沿用 Phase 1B/1A 的约束：

- Core 保持 `0.2.x` Alpha，不新增真实云 adapter、不新增商业服务。
- 不改动 ResultRecord `0.2` 的顶层字段集合；Phase 1C 只在既有契约之上做编排与
  只读聚合。
- 纯标准库，不引入 numpy/scipy。
- Pro 只通过公开接口集成，Core 不反向依赖 Pro。

## 2. 目标与非目标

### 2.1 目标

- **RunPlan**：一次基准的可重复执行规格 —— `repeat`（计入统计的测量次数）与
  `warmup`（先执行、被显式排除在统计之外的预热次数）。
- **确定性编排**：`execute_plan` 复用 Phase 1B 的 `execute()` 逐次跑，保持每次
  run 仍是一条独立、可审计、带摘要的 `0.2` 记录，绝不丢失任何一次证据。
- **统计聚合**：对同一可比组内的多条测量做纯函数聚合 —— 数值型给
  `n/mean/stdev/min/max/p50/p95/cv`，类别型给分布与一致率。
- **可比性**：只有 `fingerprints.benchmark` 且 `fingerprints.environment` 同时
  相等的记录才被聚合为同一分布；`fingerprints.implementation` 不同时聚合仍成立
  但打上「代码已变化」的告警。
- **RunPlanAggregate**：一次计划的持久化统计摘要（自带 SHA-256 摘要），引用其
  子 run_id、共享指纹与可比性判定。
- **报告增强**：`csbench report` 增量新增「重复运行统计」与「可比性」两节，
  单次运行的既有输出保持字节兼容。
- **CLI**：`csbench run` 增加 `--repeat/--warmup`。

### 2.2 非目标

- 不做跨维度的混合评分（沿用 per-dimension 原则）。
- 不做插件 API range / JSON Schema / conformance kit / workload 沙箱（Phase 1D）。
- 不做真实云 adapter、托管上传、团队报告或授权。
- 不做时间序列（series）的统计；Phase 1C 只聚合标量 `measurements`。

## 3. 数据模型

### 3.1 run 记录的计划归属（最小侵入）

`execute()` 新增可选参数 `run_context: Mapping | None = None`（默认 `None`，
行为不变）。当提供时，`_build_record` 把它写入
`extensions["core"]["run_plan"]`，因而计划归属：

- 参与 `record_digest`（可审计、不可事后篡改）；
- **不**进入三条身份指纹（warmup 与 measured、以及不同 repeat 次序，属于同一
  基准，`benchmark`/`environment` 指纹必须一致）。

`run_plan` 的键：`plan_id`、`role`（`"warmup"` | `"measured"`）、`index`、
`repeat`、`warmup`。缺省（无 `run_plan`）的记录一律视为 `measured`。

### 3.2 RunPlan

```python
@dataclass
class RunPlan:
    spec: RunSpec
    repeat: int = 1      # >=1，计入统计的测量次数
    warmup: int = 0      # >=0，先执行并排除在统计外的预热次数
```

### 3.3 RunPlanAggregate（持久化摘要）

```json
{
  "kind": "run_plan_aggregate",
  "schema_version": "0.2",
  "plan_id": "plan-YYYYMMDD-HHMMSS-xxxxxx",
  "identity": { "domain": "...", "task_id": "...", "adapter": "...", "core_version": "..." },
  "fingerprints": { "benchmark": "sha256:...", "environment": "sha256:...", "implementation": "sha256:..." },
  "comparable": true,
  "plan": { "repeat": 5, "warmup": 1 },
  "runs": { "measured": ["run-..."], "warmup": ["run-..."] },
  "status_counts": { "completed": 5 },
  "measurements": {
    "time_to_recovery_ms": {"kind":"numeric","unit":"ms","evidence":"C","n":5,
       "mean":..,"stdev":..,"min":..,"max":..,"p50":..,"p95":..,"cv":..},
    "recovery_mode": {"kind":"categorical","evidence":"C","n":5,
       "distinct":1,"mode":"auto-retry","agreement":1.0,"values":[["auto-retry",5]]}
  },
  "notes": ["..."],
  "digest": "sha256:..."
}
```

- `comparable=false` 当且仅当 measured 记录中出现 >1 个不同的
  `benchmark`+`environment` 指纹（即计划期间基准/环境实际发生了变化）；此时
  `measurements` 只聚合出现次数最多的那组，并在 `notes` 说明被排除的组。
- `digest` 覆盖除自身外的整份 payload（复用 canonical `digest`）。
- 持久化到 `results/aggregates/<domain>/<adapter>/<task>-<plan_id>.json`。
  报告加载器跳过 `aggregates/` 子树。

## 4. 统计口径（`core/statistics.py`，纯函数）

- 数值判定：`int`/`float` 且非 `bool`。混入非数值即整列按类别型处理。
- 数值型：`n`、`mean`（`fmean`）、`stdev`（`n>=2` 用样本标准差，否则 `0.0`）、
  `min`、`max`、`p50`（`median`）、`p95`（最近秩法，对小样本稳健）、
  `cv`（`stdev/mean`，`mean==0` 时为 `None`）。
- 类别型：`n`、`distinct`、`values`（按 `(-count, str(value))` 排序的
  `[value, count]` 列表）、`mode`、`agreement`（`最高频次 / n`）。
- `unit`/`evidence` 跨记录若一致则透传，不一致则置空并在 `notes` 记「mixed」。
- 只聚合 `status ∈ {completed, unsupported}` 的 measured 记录；失败/无效记录计入
  `status_counts` 但不进入分布（它们没有可信测量）。

## 5. 编排（`core/runplan.py`）

`execute_plan(plan, results_dir=None, enrich=True, preflight=True, publisher=None,
debug=False, plan_id=None) -> RunPlanAggregate`：

1. 生成 `plan_id`（`plan-<utc>-<rand6>`）。
2. 先跑 `warmup` 次（`role="warmup"`），再跑 `repeat` 次（`role="measured"`），
   每次都经 `execute()` 落一条独立记录。
3. 用 measured 记录构建 `RunPlanAggregate`，持久化摘要，返回。

单次失败不终止计划：某次 run 落 `failed`/`invalid` 记录后继续，最终在
`status_counts` 与 `notes` 中如实体现。

## 6. 报告增强（增量、向后兼容）

`generate_report` 现有「per-dimension 矩阵（latest-per-cell）+ 红旗」保持不变。
新增两节，仅在存在可聚合数据时出现：

- **## 重复运行统计**：按 `(domain, task, adapter, benchmark_fp, environment_fp)`
  聚合所有 measured 记录（排除 warmup），`n>=1` 即展示；数值型展示
  `mean ± stdev (n=k, p95=…) [evidence]`，类别型展示 `mode ×count / n`。
- **## 可比性**：当某 `(domain, task, adapter)` 下出现 >1 个不同
  `benchmark_fp` 或 `implementation_fp` 时红旗提示「这些数字并非同一基准/同一代码，
  不可直接比较」。

## 7. CLI

`csbench run` 新增：

- `--repeat N`（默认 1）
- `--warmup W`（默认 0）

当 `repeat==1 且 warmup==0`：行为与今天完全一致（打印单条记录 JSON，退出码沿用）。
否则走 `execute_plan`，打印 `RunPlanAggregate` JSON；退出码：measured 全部
`completed`/`unsupported` → 0，否则 → 1；用户输入错误 → 2。

## 8. 测试

- `core/statistics.py`：数值/类别聚合、小样本 p95、bool 归类、mixed unit/evidence、
  cv 边界（mean=0）、空输入。
- `core/runplan.py`：warmup 被排除、repeat 计数、单次失败不终止、指纹一致、
  aggregate 摘要确定性、可比性 false 分支、持久化路径。
- `execute()` run_context：写入 `extensions.core.run_plan`、不影响指纹、默认 None
  不改变既有记录。
- `report`：既有测试保持通过；新增统计节与可比性节的断言。
- CLI：`--repeat/--warmup` 的输出与退出码。

## 9. 完成标准

- `execute_plan` 跑 `warmup+repeat` 次，落齐每条独立 `0.2` 记录，warmup 不计入统计。
- 同一可比组内的多条 measured 记录被聚合为确定性分布（含 p95 与 cv）。
- 不同 `benchmark`/`environment` 指纹绝不被混入同一分布；`implementation` 不同时
  给出告警。
- `csbench run --repeat/--warmup` 与 `csbench report` 的新节可用；单次运行输出与退出码不变。
- 纯标准库；Core 仍 `0.2.0` Alpha，无 Phase 1D 能力、无真实云 adapter。
```