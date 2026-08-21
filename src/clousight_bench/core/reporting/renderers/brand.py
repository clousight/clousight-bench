"""Clousight brand tokens + vendored assets for the default report theme.

Colors + fonts + the official 3-layer logo from the clousight web app, vendored
under clousight_bench.resources.brand and inlined so the report is self-contained.
"""

from __future__ import annotations

import base64
from functools import cache
from importlib import resources

BRAND_HSL = {
    "50": "213 100% 97%",
    "100": "214 87% 94%",
    "200": "211 80% 85%",
    "400": "210 75% 68%",
    "500": "213 73% 59%",
    "600": "217 71% 51%",
    "700": "219 52% 35%",
    "800": "220 43% 26%",
    "900": "221 45% 18%",
    "950": "222 47% 11%",
}
BG_HSL, FG_HSL = "210 40% 98%", "222 28% 15%"
AMBER_HSL, RED_HSL = "38 92% 50%", "0 72% 51%"
FONT_STACK = 'var(--font-inter), "Inter", "Noto Sans SC", system-ui, sans-serif'
# The brand name is not translated; both locales render it in English.
BRAND_NAME_ZH = "Clousight Bench"
BRAND_NAME_EN = "Clousight Bench"

_ADAPTER_PROVIDER = [
    ("aliyun", "alibaba"),
    ("alibaba", "alibaba"),
    ("aws", "aws"),
    ("huawei", "huawei"),
    ("tencent", "tencent"),
    ("gcp", "gcp"),
    ("google", "gcp"),
    ("azure", "azure"),
    ("oracle", "oracle"),
    ("ibm", "ibm"),
    ("ovh", "ovh"),
]


@cache
def logo_data_uri() -> str:
    raw = resources.files("clousight_bench.resources").joinpath("brand").joinpath("logo.png").read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


@cache
def provider_logo(adapter: str) -> str | None:
    name = None
    for prefix, provider in _ADAPTER_PROVIDER:
        if adapter.lower().startswith(prefix):
            name = provider
            break
    if name is None:
        return None
    try:
        return (
            resources.files("clousight_bench.resources")
            .joinpath("brand")
            .joinpath("providers")
            .joinpath(f"{name}.svg")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError):
        return None
