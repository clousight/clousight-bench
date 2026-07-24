# Agent-Runtime 五维测评 — 设计记录

> 2026-07-24 · 已实现。承接 backlog B 组。跑在 `local-sim`，无需云账号。

## 目标

在既有 T1.3（工具失败恢复）基础上补齐 agent-runtime 域的另外四维，使第一期「五硬测维度」在 local-sim 上端到端可复现。

## 抽象决策：能力探针 = adapter 可选方法

四维需要运行时的四类能力。为不破坏既有骨架 adapter（cn_clouds 的 NotWired），在 `AgentRuntimeAdapter` 上以**非抽象**方法承载，默认抛 `CapabilityNotSupported`（`NotImplementedError` 子类）：

| 能力方法 | 服务维度 |
|----------|----------|
| `persist_state / load_state / resume_session` | T1.2 |
| `register_tool(path, spec)` | T2.1 |
| `get_trace(session_id)` | T4.1 |
| `export_otel(session_id)` | T4.2 |

任务捕获 `CapabilityNotSupported` → 记为「不支持」的**发现**（`ok=True`），绝不崩溃——「平台没有某能力」本身是测评结果。真 adapter 必须如实暴露平台自身行为，不得模拟。

## local-sim 可配置策略（deterministic，无随机）

通过 `target` 配置，让同一套判分能观测「支持/缺失」两侧：

- `state_persistence: "durable"(默认) | "ephemeral"` —— resume 后状态是否存活。
- `tool_registration: ["mcp","openapi","native"]`（默认全支持）—— `register_tool` 返回该 path 是否在列。
- `trace.completeness: "full"(默认) | "partial"` —— partial 丢弃 TOOL spans。
- `trace.otel_export: true(默认) | false` —— false 时 `export_otel` 抛 `CapabilityNotSupported`。

## OpenInference / OTel（`openinference.py`）

- span kinds 子集：`CHAIN / LLM / TOOL`（`openinference.span.kind` 属性）。
- 完备 trace = 1 CHAIN + 1 LLM + 每个工具调用 1 TOOL span；`span_completeness = min(present, expected)/expected`。
- `to_otel` 映射为最小 OTLP `resourceSpans → scopeSpans → spans`；`validate_otel` 校验 spanId/name 非空、resource 含 service.name。

## 证据分档

| 维度 | 档 | 理由 |
|------|----|------|
| T1.2 | C | 行为可控、确定复现 |
| T1.3 | C | （既有）故障注入确定 |
| T2.1 | B | 能力观测，随 region/plan 变化 |
| T4.1 | C | 对 pinned 工具宇宙的受控 trace 测量 |
| T4.2 | B | 能力 + 格式一致性 |

## 测试

`tests/test_agent_runtime_dimensions.py`：每维一正一负（durable/ephemeral、全路径/受限、full/partial、otel on/off），共 8 例，全部经 orchestrator 端到端跑 local-sim。

## 遗留（A 组接真时）

真 adapter（阿里/AWS）接入后：跨境延迟类指标标 B 档；恢复/trace 类做 C 档硬测；`mock_tools` 需公网可达。
