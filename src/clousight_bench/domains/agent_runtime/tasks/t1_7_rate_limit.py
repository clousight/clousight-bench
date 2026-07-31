"""T1.7 rate limiting / throttle behaviour.

When demand exceeds quota, does the runtime throttle *gracefully* — a proper 429
with a Retry-After the client can honor — or silently drop work? Observe the
onset rps, the advertised Retry-After, and whether a 429 contract is honored.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter drives past quota and inspects the response; local-sim reports the
configured ``target.rate_limit = {onset_rps, retry_after_ms, honors_429}``. A
platform with no rate-limit probe yields an ``unsupported`` measurement.
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.domains.agent_runtime import permissions as perm
from clousight_bench.domains.agent_runtime.adapters.base import (
    AgentRuntimeAdapter,
    CapabilityNotSupported,
)


class RateLimitTask(Task):
    task_id = "T1.7"
    title = "Rate limiting"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.7 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_rate_limit()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "throttle_onset_rps": r.throttle_onset_rps,
                "retry_after_ms": r.retry_after_ms,
                "honors_429": r.honors_429,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "rate_limit_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.rate_limit_probe_absent",
                        severity="info",
                        summary="runtime exposes no rate-limit probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no rate-limit probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        onset = float(raw["throttle_onset_rps"])
        honors = bool(raw["honors_429"])
        findings = []
        # A runtime that throttles (onset observed) but does NOT return a proper
        # 429 + Retry-After makes clients guess -> flag it.
        if onset > 0 and not honors:
            findings.append(
                Finding(
                    code="agent_runtime.throttle_without_429",
                    severity="warning",
                    summary="throttles without a 429 / Retry-After contract",
                    evidence="B",
                    details={"throttle_onset_rps": onset},
                )
            )
        return TaskResult(
            measurements={
                "rate_limit_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "throttle_onset_rps": Measurement(
                    value=onset if onset > 0 else "none", unit="rps", evidence="B"),
                "retry_after_ms": Measurement(
                    value=raw["retry_after_ms"], unit="ms", evidence="B"),
                "honors_429": Measurement(value=honors, unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"onset={'none' if onset <= 0 else onset}rps honors_429={honors} "
                   f"retry_after={raw['retry_after_ms']}ms"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
