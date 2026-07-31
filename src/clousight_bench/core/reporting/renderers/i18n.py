"""Report UI-chrome translations (English as produced -> Chinese). Data is never
translated (platform ids, numeric values), but metric keys get a Chinese display
label. ``t()`` / ``tm()`` emit both languages; CSS + the lang toggle pick one."""
from __future__ import annotations

UI_STRINGS = {
    # panels
    "Capability matrix": "能力矩阵", "Startup latency": "启动延迟",
    "Cost (list / discount / net)": "成本(原价 / 折扣 / 净价)", "Elasticity": "弹性伸缩",
    "Fault recovery": "故障恢复", "State persistence": "状态持久化",
    "Observability": "可观测性",
    # chrome
    "platform": "平台", "capability": "能力", "profile": "画像",
    "Data: locally collected": "数据:本地采集", "Clousight Bench report": "指北测评报告",
    # domains
    "agent-runtime": "智能体运行时", "bigdata-emr": "大数据",
    # capability labels
    "state-persistence": "状态持久化", "trace": "链路追踪", "otel-export": "OTel 导出",
    "elasticity": "弹性", "tool:mcp": "工具:MCP", "tool:openapi": "工具:OpenAPI",
    "tool:native": "工具:原生",
    # executions
    "simulated": "模拟", "live": "真云", "unknown": "未知",
}

METRIC_LABELS = {
    "provision_ready_ms": "部署就绪(ms)", "cold_start_ms": "冷启动(ms)",
    "warm_start_p50_ms": "热启动 p50(ms)", "warm_start_p95_ms": "热启动 p95(ms)",
    "cold_warm_ratio": "冷热比",
    "list_cost_usd": "原价(USD)", "discount_usd": "折扣(USD)", "cost_usd": "净价(USD)",
    "invocations": "调用次数", "vcpu_hours": "vCPU 小时", "duration_ms": "时长(ms)",
    "concurrency_knee": "并发拐点", "success_rate_at_peak": "峰值成功率",
    "p95_ms_at_peak": "峰值 p95(ms)", "max_concurrency_tested": "最大测试并发",
    "recovery_mode": "恢复策略", "total_attempts": "总尝试数",
    "time_to_recovery_ms": "恢复耗时(ms)", "fault_hits": "故障命中",
    "persistence_mode": "持久化模式", "state_persisted": "状态存活",
    "span_completeness": "Span 完整度", "otel_valid": "OTel 合法", "span_count": "Span 数",
}


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _dual(zh: str, en: str) -> str:
    return (f"<span class='i18n'><span class='zh'>{_esc(zh)}</span>"
            f"<span class='en'>{_esc(en)}</span></span>")


def t(en: str) -> str:
    return _dual(UI_STRINGS.get(en, en), en)


def tm(metric_key: str) -> str:
    return _dual(METRIC_LABELS.get(metric_key, metric_key), metric_key)
