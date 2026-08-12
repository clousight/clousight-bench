"""T4.3 metrics & log completeness.

Traces (T4.1/T4.2) are only one telemetry signal. A runtime you can actually
operate also exports metrics (invocations, latency, errors, saturation) and
structured logs. Observe how many of the expected metric/log signals are present
and whether logs are structured (queryable) rather than free text.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter enumerates the exported signals; local-sim reports the configured
``target.signals``. No signals probe -> ``unsupported``, never a crash.
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


def _ratio(present: int, expected: int) -> float:
    return round(present / expected, 4) if expected > 0 else 1.0


class SignalCompletenessTask(Task):
    task_id = "T4.3"
    title = "Metrics & logs"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("observability/metrics-logs",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T4.3 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_signals()
        except CapabilityNotSupported as exc:
            return ObservationBundle(observations={"capability": "unsupported", "reason": str(exc)})
        return ObservationBundle(
            observations={
                "capability": "supported",
                "metrics_present": r.metrics_present,
                "metrics_expected": r.metrics_expected,
                "logs_present": r.logs_present,
                "logs_expected": r.logs_expected,
                "structured_logs": r.structured_logs,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={"signals_capability": Measurement(value="unsupported", unit="", evidence="B")},
                findings=[
                    Finding(
                        code="agent_runtime.signals_probe_absent",
                        severity="info",
                        summary="runtime exposes no metrics/logs probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no metrics/logs probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        metrics_completeness = _ratio(raw["metrics_present"], raw["metrics_expected"])
        logs_completeness = _ratio(raw["logs_present"], raw["logs_expected"])
        structured = bool(raw["structured_logs"])
        findings = []
        if metrics_completeness < 1.0 or logs_completeness < 1.0:
            findings.append(
                Finding(
                    code="agent_runtime.signals_incomplete",
                    severity="warning",
                    summary="metrics or logs are incomplete",
                    evidence="B",
                    details={
                        "metrics_completeness": metrics_completeness,
                        "logs_completeness": logs_completeness,
                    },
                )
            )
        if not structured:
            findings.append(
                Finding(
                    code="agent_runtime.logs_unstructured",
                    severity="info",
                    summary="logs are unstructured (free text)",
                    evidence="B",
                    details={},
                )
            )
        return TaskResult(
            measurements={
                "signals_capability": Measurement(value="supported", unit="", evidence="B"),
                "metrics_completeness": Measurement(value=metrics_completeness, unit="", evidence="B"),
                "logs_completeness": Measurement(value=logs_completeness, unit="", evidence="B"),
                "structured_logs": Measurement(value=structured, unit="", evidence="B"),
            },
            findings=findings,
            notes=(
                f"metrics={metrics_completeness:.0%} logs={logs_completeness:.0%} structured={structured}"
            ),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
