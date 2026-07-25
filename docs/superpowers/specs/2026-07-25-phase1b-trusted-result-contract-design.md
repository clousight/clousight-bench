# Phase 1B 可信结果契约设计

## 1. 背景与决策

Phase 1A 已完成 `0.2.0` Developer Preview 的安装、CLI、adapter readiness、
reference workload 打包与 CI 基线。下一步先完成 Phase 1A 的跨仓交付闸门，再在
新的 Core 分支中完成 Phase 1B。

已确认的产品与仓库边界：

- `clousight-bench` 是公开开源仓库；此前“Phase 2 后才公开”的约定失效。
- `clousight-bench-pro` 始终为私有商业仓库。
- 两仓 `main` 必须通过 PR 与必需 CI 检查，不要求他人审批；禁止 force push 和删除。
- Phase 1B 允许一次性破坏旧 ResultRecord 与 Task API，但必须提供确定、非原地的迁移器。
- Core 保持 `0.2.x` 与 Alpha 状态，不实现真实云 adapter 或新增商业服务。

## 2. 交付闸门

### 2.1 Core Phase 1A

从 `feat/phase1a-release-baseline` 创建到 `main` 的 PR。必需检查为：

- Python 3.10、3.11、3.12、3.13 的 ruff、pytest 与本地 smoke；
- 独立 wheel-smoke，离开 checkout 验证版本、三个 reference workload 与基础运行。

Core PR 绿后合并，确认 `main` CI 通过，再建立保护规则。

### 2.2 Pro Phase 1A

Pro 新增最小 CI：

- checkout Pro 到工作区；
- checkout公开 Core `main` 到相邻 `clousight-bench` 路径；
- `uv sync --all-packages --all-extras --frozen`；
- `uv run ruff check packages`；
- `uv run pytest -q`；
- 构建 `cb-samplers` wheel 并检查 synthetic workload。

Core 合并后，Pro 从 `feat/phase1a-core-compat` 创建 PR，待 CI 通过后合并。

### 2.3 分支保护

两仓 `main`：

- 必须通过 PR 合并；
- 必须通过仓库当前 CI 的全部 required checks；
- 不要求 approving review；
- 禁止 force push；
- 禁止删除；
- 管理员同样受规则约束。

## 3. Phase 1B 目标与非目标

### 3.1 目标

- ResultRecord 升级为 schema `0.2`；
- Task 执行与评分分离；
- 生命周期故障可审计，已产生 observation 不因后续失败而丢失；
- 生成确定性的 benchmark、environment、implementation fingerprint 与 record digest；
- teardown、enricher、publisher 与 persist 失败具有明确隔离语义；
- 提供旧 schema `1.0` 到 `0.2` 的批量迁移工具；
- Core 与 Pro 只通过公开接口集成。

### 3.2 非目标

- 不实现 run-plan、warmup、repeat、统计或 comparability 报告；这些属于 Phase 1C；
- 不实现 plugin API range、JSON Schema 全集、第三方 conformance kit 或 workload
  供应链沙箱；这些属于 Phase 1D；
- 不实现真实阿里云 AgentRun；
- 不实现托管私有资产、签名、上传、团队报告或授权。

## 4. 生命周期

外部可观察生命周期为：

```text
RESOLVE
→ VALIDATE
→ PREFLIGHT
→ SETUP
→ EXECUTE
→ COLLECT
→ SCORE
→ ENRICH
→ PERSIST
→ optional PUBLISH
```

职责：

- `RESOLVE`：解析 DomainPack、Task、ProviderAdapter 与 extension。
- `VALIDATE`：解析并校验 RunSpec、task params、target 与 workload manifest。
- `PREFLIGHT`：检查凭证、SDK、连通性和权限，不创建资源。
- `SETUP`：连接或创建被测资源。
- `EXECUTE`：运行 workload，产出原始事件。
- `COLLECT`：形成 `ObservationBundle`。
- `SCORE`：纯函数式地把 observation 转为 `TaskResult`。
- `ENRICH`：可选 extension 只增加派生信息。
- `PERSIST`：原子持久化核心 record。
- `PUBLISH`：默认关闭；失败写 append-only receipt，不改写核心 record。

`TEARDOWN` 是覆盖 `SETUP → COLLECT` 的强制 `finally` 清理边界，而不是一条可跳过的
业务流水线阶段。进入 SETUP 后，即使 setup 部分失败也必须调用幂等 teardown。
teardown 错误记录为阶段错误，不覆盖 execute/collect 的主错误。

## 5. Task 契约

旧接口：

```python
run(adapter, params) -> TaskOutput
```

替换为：

```python
execute(adapter, params) -> ObservationBundle
score(observations) -> TaskResult
```

`ObservationBundle` 只描述原始、可重放 observation、series 与 artifact；不得写入
评分结论。`TaskResult` 包含 measurements、findings、notes 与 task/scorer revision。
`score()` 不访问云凭证、不创建资源、不修改 observation，使历史 observation 可重新评分。

所有内置 Task 一次性迁移，不长期保留双接口。Pro 只能导入公开类型，禁止依赖 Core
下划线私有模块。

## 6. ResultRecord 0.2

顶层字段固定为：

- `schema_version`：精确值 `"0.2"`；
- `run`：run id、开始/结束时间、阶段状态；
- `identity`：domain、task、workload、adapter、Core 与插件身份版本；
- `environment`：region、mode、Python、OS 和 task 声明的非敏感环境事实；
- `fingerprints`；
- `measurements`；
- `findings`；
- `observations` 或其 artifact 指针；
- `series`；
- `artifacts`；
- `extensions`；
- `errors`；
- `status`。

`status` 只允许：

- `completed`；
- `failed`；
- `invalid`；
- `unsupported`。

`ok`、顶层 `metrics`、顶层 `evidence_layer` 与 `config_hash` 不再是新结果的权威字段。

每个 measurement 至少包含 `value`、`unit`、`evidence`，可选
`aggregation`、`sample_count`、`notes`。Finding 至少包含稳定 `code`、
`severity`、`summary`、`evidence`、`details`。

## 7. Fingerprint 与摘要

统一 `canonical_json`：

- UTF-8；
- 对象键排序；
- 无多余空白；
- 拒绝 NaN 与 Infinity；
- schema 标量确定性编码。

全部摘要使用完整 SHA-256：

- `benchmark_fingerprint`：task/scorer revision、workload/asset 身份和受控参数；
- `environment_fingerprint`：region、mode 和 task 声明的环境事实；
- `implementation_fingerprint`：Core、DomainPack、Adapter、extension 版本；
- `record_digest`：持久化 payload 内容摘要，计算时排除自身。

凭证、token、用户名、主机名和原始环境变量不得进入 record 或 fingerprint。

## 8. 错误、持久化与扩展隔离

- RESOLVE/VALIDATE 用户错误：CLI exit 2，不创建 benchmark record；
- PREFLIGHT 失败：生成 `invalid` record，不进入 SETUP；
- EXECUTE/COLLECT 失败：生成 `failed` record，保留已有 observation；
- SCORE 失败：保留 observation，记录 score stage error；
- 能力明确缺失：生成 `unsupported` status 与 finding；
- ENRICH 失败：记录 extension error，不改写核心 status；
- PUBLISH 失败：写 publish receipt，不改写核心 record；
- PERSIST：临时文件、flush/fsync、原子 rename；
- 主 results 写入失败：尝试系统临时目录 emergency JSON，并打印绝对路径。

错误对象至少包含 `stage`、`code`、`type`、`message`、`retryable`。默认 record
不保存 traceback；`--debug` 只把 traceback 写入本地日志。

Phase 1B 提供最小公开 `ResultPublisher` 边界和显式注入，不新增托管 publisher；
entry-point API range 与冲突治理留到 Phase 1D。

## 9. 迁移

新增：

```text
csbench migrate-results SOURCE --output DEST [--dry-run]
```

规则：

- 默认写入新目录，禁止原地覆盖；
- 保留原始文件路径与 SHA-256；
- `metrics` 转为 measurements；
- `evidence_layer` 下沉到 measurement/finding；
- `ok/error` 映射为 status/errors；
- `config_hash` 写入 `extensions.legacy`；
- 无法推导的 fingerprint 字段明确写 `unknown`，不得伪造；
- 输出 migration manifest，记录成功、跳过与失败；
- 重复迁移结果确定且幂等；
- 原始 metric value、series 与 artifact 指针不得丢失。

## 10. 测试与跨仓 CI

Core 必须新增：

- setup 部分失败仍 teardown；
- teardown 错误不覆盖 execute error；
- score 失败保留 observation；
- enricher/publisher 失败隔离；
- persist 主路径失败进入 emergency JSON；
- canonical JSON 与 fingerprint 确定性、NaN 拒绝和敏感字段排除；
- ResultRecord 0.2 round-trip；
- 所有内置 Task execute/score 契约测试；
- migration round-trip、幂等与数据不丢失。

Pro CI 在每次 PR 中检出 Core `main`，运行现有 enricher、resolver、sampler 与打包测试。
Phase 1B 合并前，另在 Pro 兼容分支针对 Core Phase 1B head 运行一次契约验证，不新增商业功能。

## 11. 交付顺序与完成标准

1. Phase 1A Core PR 合并并确认 `main` CI 绿；
2. Pro CI 与兼容 PR 合并；
3. 两仓 `main` 保护生效；
4. 更新“Core 已公开”的文档事实；
5. 从 Core 最新 `main` 创建 Phase 1B 分支；
6. 按独立、可回滚任务实现 Phase 1B；
7. Core CI、wheel smoke 与 Pro 契约 CI 全绿后合并。

Phase 1B 完成时：

- 新结果只写 schema `0.2`；
- 旧结果可非破坏性迁移；
- 故障注入不泄漏资源、不丢 observation；
- 每个结果可追溯 benchmark、environment 与 implementation 身份；
- Pro 继续只依赖公开 Core API；
- Phase 1C/1D、真实云与商业服务没有提前进入。
