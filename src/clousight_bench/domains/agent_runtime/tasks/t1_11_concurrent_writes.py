"""T1.11 concurrent state writes.

Two sessions simultaneously write different values to the same state key.
A safe runtime stores exactly one of the two values (last-writer-wins). A
broken runtime corrupts the state (neither value, or a garbled blend).

CapabilityNotSupported -> the platform has no state API (task returns unsupported).

Evidence layer C: deterministic, replayable.
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
from clousight_bench.domains.agent_runtime.adapters.base import AgentRuntimeAdapter, CapabilityNotSupported


class ConcurrentWritesTask(Task):
    task_id = "T1.11"
    title = "Concurrent state writes"
    evidence_layer = "C"
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)
    capability_tags = ("reliability/concurrent-writes",)
    task_revision = "1"
    scorer_revision = "1"

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sessions": 2,
            "write_key": "__concurrent_write_probe__",
        }

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        return {"state_persistence_policy": str(adapter.target.get("state_persistence", "durable"))}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.11 needs an AgentRuntimeAdapter")

        try:
            result = adapter.probe_concurrent_writes()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={
                    "capability": "unsupported",
                    "reason": str(exc),
                }
            )

        return ObservationBundle(
            observations={
                "capability": "supported",
                "write_safe": result.write_safe,
                "winner": result.winner,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "write_safe": Measurement(value=False, unit="", evidence="C"),
                    "state_capability": Measurement(value="unsupported", unit="", evidence="C"),
                },
                findings=[
                    Finding(
                        code="agent_runtime.state_api_absent",
                        severity="info",
                        summary="runtime exposes no session state persistence API",
                        evidence="C",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no state persistence; concurrent write safety not measurable",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )

        write_safe = bool(raw.get("write_safe"))
        winner = str(raw.get("winner", "unknown"))

        findings: list[Finding] = []
        if not write_safe:
            findings.append(
                Finding(
                    code="agent_runtime.concurrent_write_corruption",
                    severity="critical",
                    summary="concurrent writes to the same state key corrupted the stored value",
                    evidence="C",
                    details={"winner": winner},
                )
            )

        return TaskResult(
            measurements={
                "write_safe": Measurement(value=write_safe, unit="", evidence="C"),
                "winner": Measurement(value=winner, unit="", evidence="C"),
                "state_capability": Measurement(value="supported", unit="", evidence="C"),
            },
            findings=findings,
            notes=f"concurrent writes -> write_safe={write_safe}, winner={winner}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
