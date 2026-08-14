"""T1.14 idle-timeout config honor.

AgentRun lets you configure ``sessionIdleTimeoutSeconds`` on the runtime — after
that long with no request, the underlying instance is destroyed (and billing
stops) so the next call pays a full cold start again. This task verifies the
platform actually HONORS that knob: does the instance really recycle at the
configured timeout, or does the vendor quietly keep it warm (better latency, but
you keep paying) or drop it early (cheaper, but surprise cold starts)?

The check is deliberately cheap: provision with a SMALL idle timeout (e.g. 10s),
then do a controlled A/B on one session — idle just *under* the timeout (expect
still-warm) and idle just *over* it (expect a recycle/cold-rebuild wake). The
whole probe finishes in ~30s of wall-clock, unlike T1.5 which sweeps the
(minutes-long) platform *default* window.

This complements T1.5 (measures the undocumented default idle→cold window) by
answering the orthogonal question: is the configurable timeout truthful?

Evidence layer B: reproducible method, environment-dependent numbers. On
local-sim the verdict is a deterministic function of
``target.idle_timeout = {honored, configured_s}``.
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
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter

# A small configured timeout keeps the honor check to ~30s wall-clock: idle a few
# seconds under it (expect warm), then ~15s over it (expect recycled).
IDLE_TIMEOUT_S = 10


class IdleTimeoutHonorTask(Task):
    task_id = "T1.14"
    title = "Idle-timeout config honor"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/idle-timeout", "cost/scale-to-zero")

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "session_idle_timeout_s": IDLE_TIMEOUT_S}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.14 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe(
            "idle_timeout_honor", {"session_idle_timeout_s": IDLE_TIMEOUT_S}
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "idle_timeout_capability": Measurement(value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.idle_timeout_probe_absent",
                        severity="info",
                        summary="runtime exposes no idle-timeout honor probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no idle-timeout honor probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        configured = raw.get("configured_idle_s")
        under_ms = raw.get("under_wake_ms")
        over_ms = raw.get("over_wake_ms")
        honored = bool(raw.get("honored"))

        findings: list[Finding] = []
        if not honored:
            findings.append(
                Finding(
                    code="agent_runtime.idle_timeout_not_honored",
                    severity="warning",
                    summary=(
                        f"platform did not honor the configured {configured}s session idle "
                        "timeout: the instance was not recycled shortly after the timeout "
                        "(stays warm — better latency but you keep paying — or was already "
                        "cold under the timeout)"
                    ),
                    evidence="B",
                    details={
                        "configured_idle_s": configured,
                        "under_wake_ms": under_ms,
                        "over_wake_ms": over_ms,
                    },
                )
            )

        measurements: dict[str, Measurement] = {
            "idle_timeout_capability": Measurement(value="supported", unit="", evidence="B"),
            "configured_idle_s": Measurement(value=configured, unit="s", evidence="B"),
            "under_wake_ms": Measurement(value=under_ms, unit="ms", evidence="B"),
            "over_wake_ms": Measurement(value=over_ms, unit="ms", evidence="B"),
            "idle_timeout_honored": Measurement(value=honored, unit="", evidence="B"),
            "cold_start_ms": Measurement(value=raw.get("cold_start_ms"), unit="ms", evidence="B"),
        }
        notes = (
            f"configured={configured}s honored={honored} "
            f"(under-timeout wake={under_ms}ms, over-timeout wake={over_ms}ms)"
        )
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=notes,
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
