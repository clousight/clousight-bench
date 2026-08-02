"""T4.4 span-parent propagation.

A complete trace (T4.1) is not enough if context breaks across tool calls: spans
whose parent id points nowhere (orphans) or a trace with several roots make the
call graph unreadable. Observe orphaned spans and the root-span count — a clean
trace has zero orphans and exactly one root.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter inspects the emitted trace tree; local-sim reports the configured
``target.span_propagation``. No probe -> ``unsupported``, never a crash.
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


class SpanPropagationTask(Task):
    task_id = "T4.4"
    title = "Span propagation"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("observability/span-propagation",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.4 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_span_propagation()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "spans": r.spans,
                "orphan_spans": r.orphan_spans,
                "root_count": r.root_count,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "propagation_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.propagation_probe_absent",
                        severity="info",
                        summary="runtime exposes no span-propagation probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no span-propagation probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        spans = int(raw["spans"])
        orphans = int(raw["orphan_spans"])
        roots = int(raw["root_count"])
        correctness = round((spans - orphans) / spans, 4) if spans > 0 else 1.0
        findings = []
        if orphans > 0 or roots != 1:
            findings.append(Finding(
                code="agent_runtime.broken_span_propagation", severity="warning",
                summary="orphaned spans or multiple trace roots", evidence="B",
                details={"orphan_spans": orphans, "root_count": roots}))
        return TaskResult(
            measurements={
                "propagation_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "parent_correctness": Measurement(
                    value=correctness, unit="", evidence="B"),
                "orphan_spans": Measurement(value=orphans, unit="", evidence="B"),
                "root_count": Measurement(value=roots, unit="", evidence="B"),
            },
            findings=findings,
            notes=f"correctness={correctness:.0%} orphans={orphans} roots={roots}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
