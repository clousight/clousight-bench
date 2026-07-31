"""Report UI-chrome translations (English as produced -> Chinese). Data is never
translated. ``t()`` emits both languages; CSS + the lang toggle pick one."""
from __future__ import annotations

UI_STRINGS = {
    "Capability matrix": "能力矩阵",
    "Startup latency": "启动延迟",
    "Cost (list / discount / net)": "成本(原价 / 折扣 / 净价)",
    "Elasticity": "弹性伸缩",
    "Fault recovery": "故障恢复",
    "State persistence": "状态持久化",
    "Observability": "可观测性",
    "platform": "平台",
    "capability": "能力",
    "Data: locally collected": "数据:本地采集",
    "Clousight Bench report": "指北测评报告",
}


def _esc(s: object) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def t(en: str) -> str:
    zh = UI_STRINGS.get(en, en)
    return (f"<span class='i18n'><span class='zh'>{_esc(zh)}</span>"
            f"<span class='en'>{_esc(en)}</span></span>")
