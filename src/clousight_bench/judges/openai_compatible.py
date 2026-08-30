"""OpenAI-compatible judge provider — the open-source reference judge.

Builds an :class:`EndpointJudge` (SSRF-guarded) from run config, config-connecting
to any OpenAI-compatible ``/chat/completions`` endpoint. Commercial judges ship
their own :class:`JudgeProvider` from a private package under the same
``clousight_bench.judges`` entry-point group.

Config shape (e.g. under a run's ``params.judge``)::

    {"provider": "openai-compatible", "endpoint": "https://.../v1",
     "model": "qwen-max", "credentials_ref": "env:DASHSCOPE_API_KEY"}
"""

from __future__ import annotations

import os
from typing import Any

from clousight_bench.core.judge import JudgeModel, JudgeProvider


def _resolve_api_key(credentials_ref: str) -> str:
    """Resolve an ``env:VAR`` credentials reference; else fall back to common vars.
    Never accepts an inline secret (the reference names an env var, not the key)."""
    ref = str(credentials_ref or "")
    if ref.startswith("env:"):
        return os.environ.get(ref[4:], "")
    for var in ("OPENAI_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    return ""


class OpenAiCompatibleJudgeProvider(JudgeProvider):
    name = "openai-compatible"

    def build(self, config: dict[str, Any]) -> JudgeModel:
        from clousight_bench.suites._llm_shared import EndpointJudge

        endpoint = str(config.get("endpoint") or "")
        model = str(config.get("model") or "")
        if not endpoint or not model:
            raise RuntimeError("openai-compatible judge needs 'endpoint' + 'model' in the judge config")
        return EndpointJudge(  # validates the endpoint (SSRF guard) at construction
            endpoint=endpoint,
            model=model,
            api_key=_resolve_api_key(config.get("credentials_ref", "")),
        )
