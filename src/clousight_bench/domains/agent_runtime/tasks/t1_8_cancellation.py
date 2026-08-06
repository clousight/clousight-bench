"""T1.8 timeout & cancellation correctness.

When a request is cancelled or times out, two things must hold: the work is
actually stopped (honored), and cleanup still runs so nothing is orphaned
(teardown ran, no residual). A runtime that acks a cancel but keeps billing/
running, or that skips teardown on the cancel path, is a correctness hazard.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter issues a cancel mid-flight and inspects the outcome; local-sim reports
the configured ``target.cancellation = {honors_cancel, teardown_on_cancel,
residual_on_cancel}``. No cancel probe -> ``unsupported``, never a crash.
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


class CancellationTask(Task):
    task_id = "T1.8"
    title = "Timeout & cancellation"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/cancellation",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.8 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("cancellation", {})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "cancellation_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.cancellation_probe_absent",
                        severity="info",
                        summary="runtime exposes no cancellation probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no cancellation probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        honored = bool(raw["honored"])
        teardown = bool(raw["teardown_ran"])
        residual = list(raw["residual"])
        findings = []
        if not honored:
            findings.append(Finding(
                code="agent_runtime.cancel_not_honored", severity="warning",
                summary="cancel/timeout did not stop the work", evidence="B",
                details={}))
        if not teardown or residual:
            findings.append(Finding(
                code="agent_runtime.cancel_teardown_leak", severity="warning",
                summary="teardown skipped or resources leaked on cancel", evidence="B",
                details={"teardown_ran": teardown, "residual": residual}))
        return TaskResult(
            measurements={
                "cancellation_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "cancellation_honored": Measurement(
                    value=honored, unit="", evidence="B"),
                "teardown_on_cancel": Measurement(
                    value=teardown, unit="", evidence="B"),
                "residual_on_cancel": Measurement(
                    value=len(residual), unit="", evidence="B"),
            },
            findings=findings,
            notes=(f"honored={honored} teardown={teardown} residual={len(residual)}"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
