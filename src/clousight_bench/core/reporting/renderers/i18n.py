"""Report UI-chrome translations (English as produced -> Chinese). Data is never
translated (platform ids, numeric values), but metric keys get a Chinese display
label. ``t()`` / ``tm()`` emit both languages; CSS + the lang toggle pick one."""
from __future__ import annotations

UI_STRINGS = {
    # tabs
    "Performance": "性能", "Reliability": "可靠性", "Observability": "可观测",
    "Cost": "成本", "Capability": "能力",
    # panels
    "Capability matrix": "能力矩阵", "Startup latency": "启动延迟",
    "Sustained load & tail latency": "持续负载与尾延迟", "Warm-pool retention": "热池保活",
    "Provisioning lifecycle": "部署生命周期", "Tracing": "链路追踪",
    "Tool registration": "工具注册",
    "Soak availability": "长稳可用性", "Rate limiting": "限流",
    "Timeout & cancellation": "超时与取消",
    "Metrics & logs": "指标与日志", "Span propagation": "跨度传播",
    "Export latency": "导出延迟",
    "Idle / scale-to-zero": "空闲 / 缩容到零", "Concurrency ceiling": "并发上限",
    "Tenant isolation": "租户隔离",
    "Cost (list / discount / net)": "成本(原价 / 折扣 / 净价)", "Elasticity": "弹性伸缩",
    "Fault recovery": "故障恢复", "State persistence": "状态持久化",
    # chrome
    "platform": "平台", "capability": "能力", "profile": "画像",
    "Overview": "平台总览", "Summary": "评测小结", "Red flags": "风险提示",
    "Generated": "生成时间", "Source": "数据源", "no data": "暂无数据",
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
    "provision_ready_ms": "部署就绪(ms)", "teardown_ms": "销毁(ms)",
    "residual_count": "残留资源数", "cold_start_ms": "冷启动(ms)",
    "warm_start_p50_ms": "热启动 p50(ms)", "warm_start_p95_ms": "热启动 p95(ms)",
    "cold_warm_ratio": "冷热比",
    "throughput_rps": "吞吐(rps)", "p50_ms": "p50(ms)", "p99_ms": "p99(ms)",
    "jitter_ms": "抖动(ms)", "error_rate_under_load": "负载错误率",
    "warm_retention_ms": "保活时长(ms)", "keeps_warm": "保持热实例",
    "list_cost_usd": "原价", "discount_usd": "折扣", "cost_usd": "净价",
    "invocations": "调用次数", "vcpu_hours": "vCPU 小时", "duration_ms": "时长(ms)",
    "scales_cleanly": "平滑扩缩", "concurrency_knee": "并发拐点",
    "success_rate_at_peak": "峰值成功率",
    "p95_ms_at_peak": "峰值 p95(ms)", "max_concurrency_tested": "最大测试并发",
    "recovery_mode": "恢复策略", "total_attempts": "总尝试数",
    "time_to_recovery_ms": "恢复耗时(ms)", "fault_hits": "故障命中",
    "budgeted_success": "预算内成功",
    "persistence_mode": "持久化模式", "state_persisted": "状态存活",
    "availability": "可用性", "soak_error_rate": "长稳错误率", "soak_requests": "长稳请求数",
    "throttle_onset_rps": "限流起点(rps)", "retry_after_ms": "Retry-After(ms)",
    "honors_429": "返回 429", "cancellation_honored": "取消生效",
    "teardown_on_cancel": "取消时清理", "residual_on_cancel": "取消残留数",
    "span_completeness": "Span 完整度", "spans_present": "实到 Span",
    "spans_expected": "应到 Span", "otel_valid": "OTel 合法", "span_count": "Span 数",
    "metrics_completeness": "指标完整度", "logs_completeness": "日志完整度",
    "structured_logs": "结构化日志", "parent_correctness": "父子正确率",
    "orphan_spans": "孤儿 Span", "root_count": "根 Span 数",
    "export_latency_ms": "导出延迟(ms)", "dropped_ratio": "丢弃率",
    "mcp": "MCP", "openapi": "OpenAPI", "native": "原生", "supported_count": "支持数",
    "scales_to_zero": "缩容到零", "idle_cost_per_hour": "空闲成本(USD/时)",
    "max_in_flight": "最大在途", "hard_limit": "硬限制",
    "isolation_score": "隔离评分", "tenant_isolated": "租户隔离",
    "network_egress_controlled": "出网管控", "filesystem_isolated": "文件系统隔离",
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
