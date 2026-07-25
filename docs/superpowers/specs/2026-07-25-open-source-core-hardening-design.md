# Clousight Bench 开源核心契约重整设计

> 日期：2026-07-25  
> 状态：已批准，待实施计划  
> 主要仓库：`clousight-bench`  
> 兼容验证仓库：`clousight-bench-pro`

## 1. 背景与结论

Clousight Bench 已形成正确的基本抽象：

- `DomainPack` 表达产品类别；
- `Task` 表达测评维度；
- `ProviderAdapter` 隔离云平台差异；
- `WorkloadEngine` 通过 JSONL 协议承载跨语言 workload；
- `ResultRecord`、证据分层、资产三层和 entry point 为开放生态奠定了基础。

现阶段的主要问题不是类层次错误，而是发布与可信契约尚未闭环：

- wheel 未包含 reference workloads、configs 和 sampler workload；
- CI smoke 引用了不存在的配置；
- 结果指纹缺少 task、scorer、adapter、插件和 enricher 版本；
- 单个 record 级 evidence 无法正确描述不同指标；
- report 会并列展示不可比结果，并漏报部分负面能力；
- `setup` 部分失败、teardown、score、enricher、persist 的失败隔离不足；
- skeleton adapter 与 wired adapter 在发现界面中没有区别；
- workload、artifact、remote/private asset 的边界校验不够严格；
- Pro 当前更多是契约骨架，不应牵引本轮开源核心过度商业化。

因此采用一次性“契约重整式”方案：版本退回 `0.x`，允许一次破坏性调整并提供迁移器；本轮集中完成开源核心硬化，只为商业化保留小而稳定的扩展接口。

## 2. 已确认决策

1. 采用四阶段总体路线：
   - Phase 1：OSS 契约与发布硬化；
   - Phase 2：阿里云 AgentRun 真实闭环；
   - Phase 3：Pro 混合商业闭环；
   - Phase 4：生态规模化与 1.0 稳定版准备。
2. 本设计与下一份实施计划只覆盖 Phase 1。
3. Core 版本从当前 `1.0.0` 调整为 `0.2.0`；结果 schema 升级为 `0.2`。
4. 允许一次性破坏现有结果与插件接口，但必须提供旧结果迁移器。
5. 首个真实云 Adapter 为阿里云 AgentRun，但不在 Phase 1 实现。
6. Core 仓库现已公开并采用 Apache-2.0；此前“Phase 2 完成后再公开”的决策失效。Pro 仓库仍保持私有。
7. Pro 长期采用混合模式：runner 与云凭证留在客户环境，托管服务提供私有资产、签名和团队能力；Phase 1 不实现这些服务。

## 3. Phase 1 目标与非目标

### 3.1 目标

- wheel 和 editable 两种安装方式行为一致；
- 结果可证明、可迁移、可独立评分、可判断是否可比；
- 生命周期所有阶段有明确状态、错误和资源清理语义；
- local-sim 五维和 bigdata local-process 构成可靠的开源参考基线；
- CLI 能清楚表达能力状态、配置错误、运行失败和比较限制；
- 第三方插件有版本化元数据、冲突检测和契约测试；
- Core/Pro 通过公开 API 做兼容验证，Pro 不再依赖 Core 私有符号。

### 3.2 非目标

Phase 1 不实现：

- 阿里云、AWS、华为云、火山引擎的真实 Adapter；
- 数据服务后端、用户体系、许可证、计费；
- 企业/信创 Adapter；
- 对象存储上传与数据库索引；
- Pro UI、团队治理或 SaaS 控制面；
- 分布式调度、并发矩阵执行；
- 跨机器统一时钟或大规模实验编排。

## 4. 开源与商业边界

Core 负责完整、可信、可复现的公开测评能力：

- 生命周期与资源安全；
- Domain、Task、Adapter、Workload 插件体系；
- schema、证据、指纹、迁移；
- repeat/warmup、统计与比较；
- local-sim、reference workloads；
- CLI、报告、wheel、CI、conformance kit。

Core 只保留三个商业扩展接口：

1. `ResultEnricher`：对核心结果增加成本、商业标签等派生信息；
2. `PrivateAssetResolver`：解析私有数据集和 held-out keys；
3. `ResultPublisher`：未来可选上传、签名或团队报告。

三个接口都必须满足：

- Core 不自带商业实现；
- 默认不联网、不上传；
- 扩展失败不改变核心运行状态；
- 扩展及其数据版本进入 provenance；
- 插件只依赖公开 API。

## 5. 生命周期

新生命周期为：

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

### 5.1 阶段职责

- `RESOLVE`：解析 domain、task、adapter 和插件元数据；
- `VALIDATE`：校验 RunSpec、task params、target 和 manifest；
- `PREFLIGHT`：检查凭证、SDK、连通性和权限，不创建资源；
- `SETUP`：连接或创建被测资源；
- `EXECUTE`：执行 workload，产生原始事件；
- `COLLECT`：形成 `ObservationBundle`；
- `SCORE`：纯函数式地将 observation 转为 `TaskResult`；
- `ENRICH`：可选扩展增加派生信息；
- `PERSIST`：原子写入核心结果；
- `PUBLISH`：可选扩展发布结果，默认禁用。

### 5.2 资源安全

- `ProviderAdapter.teardown()` 必须幂等；
- orchestrator 在进入 SETUP 后，无论 `setup()` 是否部分成功都调用 teardown；
- Adapter 负责记录已创建资源，使 teardown 能清理部分 setup；
- teardown 错误进入阶段错误列表，不覆盖执行或评分错误；
- 资源型 Adapter 后续必须支持 cleanup token；Phase 1 在接口和 local test double 中验证，不实现真云清理命令。

### 5.3 Task 拆分

现有：

```python
run(adapter, params) -> TaskOutput
```

调整为：

```python
execute(adapter, params) -> ObservationBundle
score(observations) -> TaskResult
```

原则：

- `execute` 负责与平台交互，不负责最终结论；
- `score` 不访问云平台，不产生副作用；
- 保存的 observation 可以离线重新评分；
- Task 声明 `task_revision` 和 `scorer_revision`；
- `CapabilityNotSupported` 转为结构化 observation/finding，不用异常表达最终报告语义。

## 6. ResultRecord 0.2

### 6.1 顶层结构

`ResultRecord` 包含：

- `schema_version`；
- `run`：run id、开始/结束时间、阶段状态；
- `identity`：domain、task、workload、adapter、插件身份与版本；
- `environment`：region、mode、Python/OS 和任务声明的环境事实；
- `fingerprints`；
- `measurements`；
- `findings`；
- `observations` 或其 artifact 指针；
- `series`；
- `artifacts`；
- `extensions`；
- `errors`；
- `status`。

### 6.2 运行状态

`status` 仅允许：

- `completed`：执行与评分完成；
- `failed`：平台或 workload 执行失败；
- `invalid`：输入、preflight 或实验完整性不成立；
- `unsupported`：平台明确不支持该维度所需能力。

`ok: bool` 不再作为权威状态；迁移器可生成兼容派生值。

### 6.3 Measurement

每项 measurement 至少包含：

```json
{
  "value": 87.2,
  "unit": "ms",
  "evidence": "B"
}
```

可选字段：

- `aggregation`：raw、mean、median、p95 等；
- `sample_count`；
- `notes`。

证据属于 measurement 或 finding，不再强制整条 record 只有一个 evidence layer。例如 T1.3 的恢复模式可以是 C，而云环境时延为 B。

### 6.4 Finding

Finding 包含：

- `code`：稳定机器码；
- `severity`：`info | warning | critical`；
- `summary`；
- `evidence`；
- `details`。

`unsupported`、`ephemeral`、`invalid_trace`、`otel_invalid`、`no_fault_observed` 等必须进入 findings。报告不再依赖硬编码少数 metric key 判断红旗。

### 6.5 Fingerprint

不再用一个 `config_hash` 混合所有语义：

- `benchmark_fingerprint`：task/scorer revision、workload、资产身份、受控参数；
- `environment_fingerprint`：region、mode 和 task 声明的环境事实；
- `implementation_fingerprint`：Core、DomainPack、Adapter、插件版本；
- `record_digest`：持久化 payload 的内容摘要。

全部使用完整 SHA-256。CLI 与报告只显示短码。

所有 fingerprint 使用同一 `canonical_json` 实现：UTF-8、对象键排序、无多余空白、拒绝 NaN/Infinity，并对 schema 允许的标量做确定性编码。`record_digest` 计算时排除自身字段，避免循环摘要。environment 只接受任务声明的非敏感事实，凭证、用户名、主机名和原始环境变量不得进入 fingerprint。

旧 `config_hash` 在迁移结果中保留在 `extensions.legacy`，新结果不把它作为权威比较键。

## 7. 可比性与统计

### 7.1 可比性规则

- `benchmark_fingerprint` 不同：不可直接比较，必须分组；
- benchmark 相同、environment 不同：可以作为环境观察并列，但报告必须显式标注；
- implementation 不同：允许比较，但必须展示版本差异；
- schema 无法迁移：拒绝比较；
- status 为 invalid：不得参与统计；
- status 为 failed/unsupported：保留在报告中，但不参与数值聚合。

比较器返回结构化 `ComparabilityDecision`，包括：

- `comparable`；
- `level`：`strict | environment-observation | incompatible`；
- `reasons`；
- `differing_fields`。

### 7.2 Run plan

新增 `csbench run-plan plan.yaml`，Phase 1 最小支持：

- 多个 task；
- `warmup`；
- `repeats`；
- 固定声明顺序；
- 顺序执行；
- 失败后继续或停止策略。

Phase 1 不支持并发和分布式执行。

### 7.3 统计

对可比较且成功的重复结果，最小输出：

- count；
- median；
- p95；
- min/max。

median 与 p95 使用文档化的确定性 nearest-rank 规则；样本不足时仍可计算，但必须展示 sample count，不生成置信区间。

单次运行仍可报告，但必须标注 `sample_count=1`，不得暗示统计稳定性。

## 8. 插件 SDK

### 8.1 元数据

`DomainPack` 声明：

- `name`；
- `version`；
- `api_range`。

Task 声明：

- `task_id`；
- `task_revision`；
- `scorer_revision`；
- `title`。

Adapter 声明：

- `name`；
- `version`；
- `provider`；
- `status`：`reference | experimental | wired | skeleton`；
- `capabilities`。

### 8.2 加载规则

- entry point name、domain name、enricher name 重复时直接报错；
- 插件 API range 与 Core 不兼容时拒绝加载；
- skeleton 可被发现，但默认不可执行；
- `PrivateAssetResolver` 增加 `supports(spec)`，按能力路由；
- `ResultEnricher` 和 `ResultPublisher` 按稳定名称排序；
- 每个 enricher 的名称、版本和执行结果写入 `extensions`；
- publisher 在核心结果原子持久化后执行，其成功或失败写入独立 publish receipt，不回写已持久化核心 record。

### 8.3 Conformance kit

提供 `clousight_bench.testing`：

- DomainPack 元数据测试；
- Adapter lifecycle/teardown 幂等测试；
- Task execute/score 分离测试；
- entry point 兼容测试；
- resolver routing 测试；
- enricher/publisher 失败隔离测试；
- ResultRecord schema round-trip 测试。

Pro CI 只需安装 Core 与现有 Pro 包并运行该套件，不新增商业功能。

## 9. 配置、Workload 与资产协议

### 9.1 JSON Schema

发布并测试：

- RunSpec schema；
- ResultRecord 0.2 schema；
- workload manifest schema；
- run plan schema。

Core 继续保持轻依赖；运行时使用显式解析和校验函数，JSON Schema 作为对外契约与编辑器支持，不强制引入重量级运行时依赖。

### 9.2 Workload JSONL

- 每个 workload manifest 声明 `protocol_version`；
- Phase 1 接受旧协议并迁移到当前事件模型；
- metric/sample/artifact/result 事件必须校验必需字段和类型；
- stdout 使用流式读取并设置事件数、单行大小和总输出限制；
- manifest 中 params schema 从说明升级为运行前校验。

### 9.3 路径与资产安全

- artifact 只能位于显式 output directory 内；
- 拒绝绝对路径、`..` 越界和符号链接逃逸；
- remote/private asset 默认强制 SHA-256；
- bundled asset 允许省略 SHA-256，但发布态 reference workload 必须提供；
- cache key 包含来源、版本和完整摘要，资产名必须规范化；
- 下载失败删除 `.part`；
- params 临时文件在 finally 中删除；
- remote URI 的信任模型写入 `SECURITY.md`。

### 9.4 时序

- sample 的 `t` 统一为 run-relative monotonic seconds；
- ResultRecord 记录 wall-clock `time_origin`；
- series unit 为显式 schema 字段，不再通过 `metrics["name__unit"]` 隐式传递。

## 10. CLI 与报告

### 10.1 CLI

- `csbench list --verbose`：展示 title、revision、evidence 摘要、adapter status 和插件版本；
- `csbench validate --config ...`：仅校验配置；
- `csbench doctor`：区分 detected 与 verified；
- `csbench run`：捕获用户错误，返回稳定 exit code，不打印内部 traceback；
- `csbench run-plan`：执行顺序实验计划；
- `csbench compare`：执行可比性判断；
- `csbench migrate-results`：迁移旧结果。

Exit code：

- `0`：成功；
- `1`：工具内部错误；
- `2`：用户输入或配置错误；
- `3`：benchmark 运行失败或 invalid；
- `4`：结果不可比较；
- `5`：迁移失败。

### 10.2 报告

- 不再只取每个 cell 最新记录；
- 先按 benchmark fingerprint 分组；
- 展示 environment 与 implementation 差异；
- findings 决定红旗，不依赖固定 metric key；
- unsupported、failed、invalid 均有独立视觉状态；
- 报告继续禁止跨维度 blended score；
- Markdown 输出之外保留结构化 comparison JSON，供未来 Web 或 Pro 使用。

## 11. 错误与持久化语义

- RESOLVE/VALIDATE 用户错误：不创建 benchmark record，CLI exit 2；
- PREFLIGHT 失败：创建 invalid record，不创建资源；
- EXECUTE 失败：创建 failed record，保留已收集 observation；
- SCORE 失败：保留 observation，记录 score stage error；
- 能力缺失：生成 unsupported finding/status；
- ENRICH 失败：记录 extension error，不改变核心 status；
- PUBLISH 失败：记录 publish receipt，不改写核心 record；
- PERSIST 先写临时文件再原子 rename；
- 主 results 目录写入失败时，尝试写 emergency JSON 到系统临时恢复目录并打印绝对路径；
- emergency 写入也失败时 exit 1，并在 stderr 输出最小错误摘要。

错误对象至少包含：

- `stage`；
- `code`；
- `type`；
- `message`；
- `retryable`。

默认结果不保存 traceback；`--debug` 可将 traceback 输出到本地日志。

其中 ENRICH 错误写入核心 record 的 `extensions`；PUBLISH 因发生在核心持久化之后，错误写入同 run 目录的 append-only publish receipt。receipt 失败只影响发布命令退出状态，不改写 benchmark 结果。

## 12. 迁移

`csbench migrate-results`：

- 默认写入新目录，不原地覆盖；
- 支持 dry-run；
- 保留原始文件 SHA-256 和路径；
- 将旧 metrics 转为 measurement；
- 将 evidence_layer 下沉到 measurement/finding；
- 将旧 ok/error 映射到 status/errors；
- 旧 config_hash 放入 `extensions.legacy`；
- 无法推导的新指纹字段明确标为 `unknown`，不得伪造；
- 输出 migration manifest，记录成功、跳过与失败文件。

迁移前后必须验证：

- 原始 metric value 不丢失；
- series/artifact 指针仍可解析；
- 迁移可重复执行且结果确定。

## 13. 打包、CI 与发布

### 13.1 打包

- reference workloads 移入 package data；
- 使用 `importlib.resources` 定位，不依赖源码树层级；
- configs/examples 中真正用于运行的内容进入 wheel，纯文档示例可留仓库；
- cb-samplers 的 synthetic workload 必须进入其 wheel；
- 新增 wheel contents 测试。

### 13.2 Core CI

覆盖：

- Python 3.10、3.11、3.12、3.13；
- ruff 与 pytest；
- 最小依赖安装；
- `[store]` 安装；
- wheel 构建、全新环境安装和 smoke；
- 五维 local-sim；
- bigdata local-process；
- migrate-results；
- run-plan 与 compare；
- lifecycle fault injection；
- JSON Schema validation。

修复当前 CI 引用的缺失 `configs/bigdata-emr.local.yaml`，或删除对无必要配置的依赖；最终 smoke 命令必须在干净 checkout 和 wheel 安装态都通过。

### 13.3 Pro 契约 CI

Pro 仅增加：

- 安装指定 Core 0.2.x；
- 运行公开 conformance kit；
- 验证现有 enricher、resolver、sampler；
- 禁止 import Core 下划线私有 API。

不新增数据服务、上传、企业 Adapter 或授权实现。

### 13.4 发布治理

- 新增 `CHANGELOG.md`；
- 新增 `SECURITY.md`；
- Alpha classifier 与 `0.2.0` 保持一致；
- task/scorer 改动必须 bump revision；
- plugin/schema 破坏性变更必须更新兼容矩阵与迁移说明。

## 14. Phase 1 实施分段

### Phase 1A：恢复可发布基线

- 版本调整为 0.2.0；
- 修复 CI 缺失配置；
- 修复 Core/sampler wheel；
- 增加安装态 smoke；
- adapter status 可见；
- CLI 用户错误友好化。

### Phase 1B：可信结果契约

- ResultRecord 0.2；
- fingerprint；
- measurement/findings/errors；
- execute/score 拆分；
- 生命周期清理；
- extension 隔离；
- 迁移器。

### Phase 1C：测评方法与报告

- run-plan；
- warmup/repeat；
- 统计；
- comparability；
- report 重构；
- 修正 evidence。

### Phase 1D：插件与供应链

- 元数据和 API range；
- 冲突检测；
- resolver routing；
- workload 协议和安全边界；
- JSON Schema；
- conformance kit；
- Core/Pro 契约 CI；
- 发布文档。

实施计划应按上述顺序拆成可独立提交和回滚的任务，不并行修改同一核心契约。

## 15. 测试策略

除现有测试外，新增：

- setup 部分失败仍 teardown；
- teardown 失败不覆盖 execute error；
- score 失败保留 observation；
- enricher/publisher 失败仍持久化；
- persist 主路径失败进入 emergency 路径；
- duplicate entry point 拒绝加载；
- plugin API 不兼容拒绝加载；
- artifact path traversal/symlink escape；
- remote asset 缺 hash、hash mismatch、失败清理；
- wheel 安装后 reference workload 可运行；
- mixed evidence；
- incompatible fingerprint 不可比较；
- unsupported/invalid findings 出现在报告；
- migration round-trip 与幂等性；
- run-plan warmup 不进入正式统计；
- repeat 的 median/p95 正确。

不使用真云账号作为 Phase 1 CI 前提。

## 16. Phase 1 完成标准

- Core wheel 与 editable 安装行为一致；
- 五维 local-sim、bigdata local-process 全部运行成功；
- 故意注入 lifecycle、score、extension 和 persist 故障时不泄漏资源、不丢核心结果；
- 每个结果可追溯 task、scorer、workload、adapter、Core 和插件版本；
- measurement/finding 证据等级正确；
- 不兼容结果不能被报告直接比较；
- 旧结果可批量迁移，原始指标不丢失；
- skeleton 不会被误认为 wired；
- Pro 现有插件只依赖公开 API 并通过 conformance kit；
- Core 不实现任何新增商业功能；
- CI 在干净 checkout 和 wheel 安装态全绿；
- 文档与代码对 adapter 状态、协议、schema 和发布能力描述一致。

## 17. 后续阶段边界

### Phase 2：阿里云 AgentRun

在 Phase 1 契约上实现首个真实 Adapter、真实权限探测、公网 mock 部署方式、重复运行与公开结果合规检查。完成后才公开仓库。

### Phase 3：Pro 混合商业闭环

实现私有资产服务、结果签名、可选上传和团队报告；云凭证与 runner 留在客户环境。

### Phase 4：生态与 1.0 稳定版

增加更多真实云 Adapter、插件生态和兼容矩阵；满足稳定性门槛后发布 1.0。

