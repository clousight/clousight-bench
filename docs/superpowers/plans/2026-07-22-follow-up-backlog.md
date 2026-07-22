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

- [ ] **补齐五维**（`clousight-bench` · agent-runtime，可跑在 local-sim 上）
  - T1.2 状态持久化 / T2.1 工具注册路径（MCP·OpenAPI·native）/ T4.1 trace span 完整率（OpenInference）/ T4.2 OTel 导出兼容。
  - 每维一个 `tasks/` 文件 + 判分 + 证据档声明 + 测试，注册进 `AgentRuntimeDomain.tasks()`。
- [ ] **成本/trace 采用 OpenInference schema**；跨境延迟类指标标注证据档 B，恢复/trace 类做 C 档硬测。

## C. 评审 Minor（来自 2026-07-21 整体评审，非阻塞，择机清理）

- [ ] **`query_series` SQL 拼接改参数化**（`clousight-bench` · `core/store.py`）：`read_parquet('{pattern}')` 目前 f-string 拼接，改为 DuckDB 参数绑定或校验路径，防注入/特殊字符。
- [ ] **`unit` 列约定文档化**（`core/store.py` 长表）：`unit` 取自 `metrics["<series>__unit"]` 的隐式约定，写进 `docs/architecture.md` 数据契约节。
- [ ] **示例脚本句柄泄漏**（`clousight-bench-pro` · `cb-samplers/workloads/synthetic-sampler/run.py`）：`open(...).read()` 改为 `with` 上下文管理。
- [ ] **pricing 用量类型校验**（`clousight-bench-pro` · `cb-pricing/enricher.py`）：`qty` 非数值时给清晰错误而非静默相乘。
- [ ] **空/混合 series 边界测试**（`cb-dataservice` · `test_rollup.py`）：补空 series、多 series 混合的 rollup 用例。

## D. 交付物流转（你自己来）

- [ ] 在 GitHub `clousight` org 下创建仓库：`clousight/bench`（公开，Apache-2.0）与 `clousight-bench-pro`（私有）。
- [ ] 两个本地仓当前**无 remote**；建仓后 `git remote add origin ...` 再首推。
- [ ] `clousight-bench` 是否即刻公开：建议五维齐全 + 至少 1 家真跑数据后再 public。

## 环境备忘

- 本机 `python` 缺失、系统 `python3` 为 3.9；项目要求 >=3.10。**一律用 uv**：`uv venv --python 3.12` → `uv pip install -e ".[store,dev]"` → `uv run pytest`。
- pro 仓：`uv sync` 后需 `uv pip install pytest ruff`（dev 工具不在主依赖）。
