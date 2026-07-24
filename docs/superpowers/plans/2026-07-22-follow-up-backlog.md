# Clousight Bench 后续待办 Backlog

> 落盘于 2026-07-22。承接 `2026-07-21-clousight-bench-pro-and-data-contract.md`（已交付）。
> 本文件是「下一轮再做」的清单，按是否需要凭证 / 是否阻塞分组。勾选后即完成。

---

## A. 需要云账号 / 凭证（阻塞项，等资源到位再做）

- [ ] **AWS AgentCore adapter**（`clousight-bench` · agent-runtime 域）
  - 新建 `src/clousight_bench/domains/agent_runtime/adapters/aws_agentcore.py` → `AwsAgentCoreAdapter`。
  - 前置：确认 AWS AgentCore 账号可用（曾为 Preview 白名单）+ 核对 T&C 是否允许公开发布 benchmark 数字。
  - 契约：如实暴露平台自身的 session/重试/trace，**不得自行实现重试逻辑**；注册进 `AgentRuntimeDomain.adapters()`。
- [ ] **阿里云 AgentRun adapter 接真**（`clousight-bench`）
  - 把 `AliyunAgentRunAdapter`（现为 `_NotWiredError` 骨架）接真：SDK 调用 `create_session/run_tool_plan/destroy_session`。
  - 前置：阿里云凭证（env var，不入库）、已部署的 benchmark agent、公网可达的 `mock_tools`（隧道或小云函数）。
- [ ] **cb-adapters-enterprise 实现**（`clousight-bench-pro`）
  - 把 `NotWiredEnterpriseAdapter` 占位替换为信创/私有云 adapter；需私有云访问。

## B. 纯工程（不需凭证，可随时做）

- [x] **补齐五维**（`clousight-bench` · agent-runtime，跑在 local-sim 上）→ **已实现**
  - T1.2 状态持久化 / T2.1 工具注册路径（MCP·OpenAPI·native）/ T4.1 trace span 完整率（OpenInference）/ T4.2 OTel 导出兼容。
  - 各一个 `tasks/` 文件 + 判分 + 证据档声明 + 8 个 local-sim 测试；`AgentRuntimeDomain.tasks()` 已注册；`csbench list` 可见 T1.2/T1.3/T2.1/T4.1/T4.2。
  - adapter 基类新增 4 类能力方法（默认抛 `CapabilityNotSupported`，local-sim 按可配置策略实现）；新增 `openinference.py`（span 构建 / 完整率 / OTel 映射与校验）。
- [x] **成本/trace 采用 OpenInference schema** → **已实现**（`openinference.py`：CHAIN/LLM/TOOL span kinds + 最小 OTLP resourceSpans）。
  - 待办（需真 adapter 时）：跨境延迟类指标标注证据档 B、恢复/trace 类做 C 档硬测——留到 A 组接真时落。
- [x] **便捷凭证 + 上手脚手架**（`clousight-bench`）→ **已实现**（2026-07-24）
  - `core/credentials.py`：复用各云默认凭证链（env/profile/role），只探测来源不存密钥；`ProviderAdapter.resolve_credentials()`。
  - `csbench init <provider>`：生成私有 `*.local.yaml` + `.env.example` + 写 `.gitignore`。
  - `csbench doctor --config x`：分步体检 provider / SDK / 凭证 / mock 可达，给可操作补救。
  - 15 个新测试（credentials + init/doctor）；README「Benchmarking a real platform」与 architecture 已更新。
  - 待办（需资源时）：`csbench mock deploy/tunnel`（把 mock 一键部署成云函数 / 起隧道）——归入 A 组。
- [x] **前置校验阶段 PREFLIGHT**（`clousight-bench`）→ **已实现**（2026-07-24）
  - `core/preflight.py`：`Check`/`PreflightReport` + 可复用校验函数；`doctor` 与 orchestrator 共用（单一真源）。
  - `ProviderAdapter.preflight()`（凭证+SDK）；`AgentRuntimeAdapter.preflight()` 真云追加 mock 可达 + `check_permissions()` 钩子。
  - orchestrator 在 provision 前跑 preflight，CRITICAL 失败即早退落 `ok=False` 记录；`csbench run --skip-preflight` 可关闭。
  - 12 个新测试（report 逻辑 / 各 check / 真云早退 vs skip 后中途失败 / local-sim 正常跑）。
  - 待办（接真 adapter 时）：把各真云 adapter 的 `check_permissions()` 覆写为真实鉴权调用（STS GetCallerIdentity / RAM dry-run / GetCallerIdentity 等）——归入 A 组。
- [x] **测评集三层分发（bundled/remote/private）**（`clousight-bench` + `-pro`）→ **已实现**（2026-07-24）
  - `core/assets.py`：`AssetSpec`（source/uri/sha256/license/version）+ `resolve_asset`（bundled 相对路径校验 / remote 下载+sha256+缓存 / private 走解析器否则 `NeedLicense`）。
  - manifest `assets:` 段；`WorkloadEngine.resolve_assets()` 解析后经 `params["assets"]` 暴露；`describe()` 只折资产指纹（name@version+sha256）不含内容。
  - 扩展点 `clousight_bench.asset_resolvers` + `PrivateAssetResolver` ABC；`registry.load_asset_resolvers()`。
  - `clousight-bench-pro` · `cb-dataservice`：`DataServiceAssetResolver`（`CLOUSIGHT_BENCH_TOKEN` 鉴权 + sha256 校验），入口点注册。
  - 16 个新测试（core 12 + pro 4）；architecture 新增「测评集分发（三层）」节。
  - 公开数据集模板：`examples/asset-manifests/`（NYC taxi / SWE-bench Lite / HotpotQA remote + private held-out + bundled）+ README + 3 个解析守卫测试（不下载）。
  - 待办（接真时）：为各 remote 模板补真实 sha256；数据服务真实下载端点、held-out 判分键实际入库——归入 A 组。
- [x] **权限按 (benchmark × 云) 映射**（`clousight-bench`）→ **已实现**（2026-07-24）
  - `permissions.py` 抽象能力令牌；`Task.required_permissions` 声明每维所需令牌（与云无关）。
  - 各真云 adapter `PERMISSION_MAP`（令牌 → 该云具体最小动作）；`required_actions(task)` + `_probe_permissions(actions)`（默认 WARNING 列最小动作，接真覆写为鉴权调用 → 缺失 CRITICAL）。
  - orchestrator/preflight 传入 task；`csbench doctor --domain/--platform/--task` 打印该 (benchmark×云) 最小动作清单。
  - 6 个新测试（跨 task / 跨云动作不同 / 未映射令牌告警 / 列最小动作 / 模拟已接真时缺失即 CRITICAL）。
  - 待办：接真时把 `_probe_permissions` 覆写为各云真实策略模拟/dry-run——归入 A 组。

## C. 评审 Minor（来自 2026-07-21 整体评审）— **已全部清理**

- [x] **`query_series` 免注入**（`core/store.py`）：改用 DuckDB 关系 API `con.read_parquet(pattern).create_view(...)`（DDL 不支持参数占位符），路径作为 Python 参数传入，杜绝字符串拼接。
- [x] **`unit` 列约定文档化**（`docs/architecture.md` 数据契约节）：`unit` 取自 `metrics["<series>__unit"]`，隐式非强制。
- [x] **示例脚本句柄**（`cb-samplers/.../run.py`）：`open(...).read()` → `with` + `json.load`。
- [x] **pricing 用量类型校验**（`cb-pricing/enricher.py`）：`qty` 非数值（含 bool）抛清晰 `TypeError`。
- [x] **空/混合 series 边界测试**（`cb-dataservice/test_rollup.py`）：补空 series、多 series、缺文件三个用例。

## D. 交付物流转（你自己来）

- [ ] 在 GitHub `clousight` org 下创建仓库：`clousight/bench`（公开，Apache-2.0）与 `clousight-bench-pro`（私有）。
- [ ] 两个本地仓当前**无 remote**；建仓后 `git remote add origin ...` 再首推。
- [ ] `clousight-bench` 是否即刻公开：建议五维齐全 + 至少 1 家真跑数据后再 public。

## 环境备忘

- 本机 `python` 缺失、系统 `python3` 为 3.9；项目要求 >=3.10。**一律用 uv**：`uv venv --python 3.12` → `uv pip install -e ".[store,dev]"` → `uv run pytest`。
- pro 仓：`uv sync` 后需 `uv pip install pytest ruff`（dev 工具不在主依赖）。
