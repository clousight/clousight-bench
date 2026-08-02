"""T0.1 provisioning (deploy) latency.

How long from asking the platform to stand up a runtime instance to that
instance being ready to serve? ``execute`` provisions one instance from the
benchmark artifact, records the platform's own create->ready latency, then
tears it back down so the deploy probe never leaks a resource. ``score`` turns
that into a measurement; a runtime with no provisioning API records the absence
as a finding (never a crash).

Evidence layer B: the method is reproducible, but the number is
environment-dependent (region, image pull, cold capacity). On mock the cost is
a deterministic knob (``target.provision.ready_ms``) so scoring can be exercised
with no account.
"""
from __future__ import annotations

import contextlib
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


class ProvisionLatencyTask(Task):
    task_id = "T0.1"
    title = "Provisioning (deploy) latency"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.PROVISION, perm.DEPROVISION)
    capability_tags = ("performance/provisioning",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def _artifact_spec(self, adapter: AgentRuntimeAdapter) -> dict[str, Any]:
        return {
            "artifact_ref": str(
                adapter.target.get("artifact_ref") or adapter.target.get("agent_id") or ""
            )
        }

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T0.1 needs an AgentRuntimeAdapter")
        try:
            result = adapter.provision(self._artifact_spec(adapter))
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        # Tear the probed instance down so measuring deploy latency never leaks a
        # runtime. A teardown failure is T0.2's concern, not T0.1's -- it must not
        # corrupt this deploy-latency observation, so it is suppressed here.
        with contextlib.suppress(Exception):
            adapter.deprovision(result.runtime_id)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "ready": result.ready,
                "ready_latency_ms": result.ready_latency_ms,
                "runtime_id": result.runtime_id,
                "artifact_ref": result.artifact_ref,
            },
            series={"provision_ready_ms": [[1, result.ready_latency_ms]]},
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "provision_capability": Measurement(
                        value="unsupported", unit="", evidence="B"
                    ),
                },
                findings=[
                    Finding(
                        code="agent_runtime.provision_api_absent",
                        severity="info",
                        summary="runtime exposes no provisioning (deploy) API",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no provisioning API",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        ready_ms = raw.get("ready_latency_ms")
        ready = bool(raw.get("ready"))
        findings = (
            []
            if ready
            else [
                Finding(
                    code="agent_runtime.provision_not_ready",
                    severity="warning",
                    summary="provisioned runtime did not reach a ready state",
                    evidence="B",
                    details={"runtime_id": raw.get("runtime_id", "")},
                )
            ]
        )
        return TaskResult(
            measurements={
                "provision_ready_ms": Measurement(
                    value=ready_ms, unit="ms", evidence="B"
                ),
                "provision_ready": Measurement(value=ready, unit="", evidence="B"),
            },
            findings=findings,
            notes=f"provision create->ready = {ready_ms}ms (ready={ready})",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
