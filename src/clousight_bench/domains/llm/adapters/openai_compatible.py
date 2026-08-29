"""OpenAI-compatible LLM endpoint adapters for the `llm` domain.

The SUT-connection abstraction for a managed LLM is its OpenAI-compatible
endpoint, surfaced through the run ``Target``:

- ``LlmMockAdapter`` (``llm-mock``): an offline reference — the suite's mock path
  replays a bundled answer fixture; no network, no model.
- ``LlmEndpointAdapter`` (``llm-endpoint``): config-connect to an ALREADY-RUNNING
  managed LLM — ``endpoint`` (OpenAI-compatible base URL), ``model``, and
  ``credentials_ref`` (e.g. ``env:DASHSCOPE_API_KEY``) from the ``Target``. Works
  for Bedrock/DashScope/Vertex/OpenAI/any OpenAI-compatible gateway.

No SDK dependency: the suite POSTs to ``/chat/completions`` with stdlib/`requests`
lazily; preflight checks config presence and fails loud (skipped for mock runs).
"""

from __future__ import annotations

import os
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter, Task


class _LlmAdapterBase(ProviderAdapter):
    """Shared LLM adapter: expose the model + resolve the API key reference."""

    def model(self) -> str:
        return str(self.target.get("model") or "")

    def api_key(self) -> str:
        """Resolve the API key from ``credentials_ref`` (``env:VAR``) or env fallbacks."""
        ref = str(self.target.get("credentials_ref") or "")
        if ref.startswith("env:"):
            return os.environ.get(ref[4:], "")
        for var in ("OPENAI_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY"):
            if os.environ.get(var):
                return os.environ[var]
        return ""

    def _is_mock(self) -> bool:
        return str(self.target.get("mode", "")).lower() == "mock" or bool(self.target.get("mock"))


class LlmMockAdapter(_LlmAdapterBase):
    """Offline reference — the suite replays a bundled answer fixture."""

    name = "llm-mock"
    status = "reference"
    provider = None
    target_example: dict = {"limit": 15}

    def execution_mode(self) -> str:
        # No real model is queried — a simulated reference, never pooled with
        # numbers from a live endpoint.
        return "simulated"


class LlmEndpointAdapter(_LlmAdapterBase):
    """Config-connect to an already-running managed LLM (OpenAI-compatible)."""

    name = "llm-endpoint"
    status = "experimental"
    provider = None
    target_example: dict = {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "credentials_ref": "env:DASHSCOPE_API_KEY",
        "limit": 100,
    }

    def preflight(self, task: Task | None = None) -> Any:
        from clousight_bench.core import preflight as pf

        report = pf.PreflightReport()
        if self._is_mock():
            report.add(
                pf.Check("llm", ok=True, severity=pf.WARNING, detail="mock run — endpoint not required")
            )
            return report
        if not str(self.target.get("endpoint") or ""):
            report.add(
                pf.Check(
                    "llm-endpoint",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail="no `endpoint` (OpenAI-compatible base URL) in target",
                    remediation="set target.endpoint + target.model + target.credentials_ref",
                )
            )
        else:
            report.add(pf.Check("llm-endpoint", ok=True, severity=pf.CRITICAL, detail="endpoint configured"))
        if not self.api_key():
            report.add(
                pf.Check(
                    "llm-credentials",
                    ok=False,
                    severity=pf.CRITICAL,
                    detail="no API key resolved from credentials_ref",
                    remediation="set target.credentials_ref=env:YOUR_KEY_VAR and export it",
                )
            )
        else:
            report.add(pf.Check("llm-credentials", ok=True, severity=pf.CRITICAL, detail="api key resolved"))
        return report
