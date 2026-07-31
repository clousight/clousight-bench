"""T5.4 concurrency ceiling.

T5.2 finds where performance *bends*; this reports the *admitted ceiling* — the
highest number of concurrent invocations the runtime accepts — and whether it is
a hard cap (requests beyond it are rejected) or soft/burstable. A low hard cap
constrains how far a workload can scale regardless of latency.

Evidence layer B: method reproducible, numbers environment-dependent. A real
adapter ramps concurrency until admission fails; local-sim reports the configured
``target.ceiling``. No probe -> ``unsupported``, never a crash.
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.observation import (
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


class ConcurrencyCeilingTask(Task):
    task_id = "T5.4"
    title = "Concurrency ceiling"
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
            raise TypeError("T5.4 needs an AgentRuntimeAdapter")
        try:
            r = adapter.probe_concurrency_ceiling()
        except CapabilityNotSupported as exc:
            return ObservationBundle(
                observations={"capability": "unsupported", "reason": str(exc)}
            )
        return ObservationBundle(
            observations={
                "capability": "supported",
                "max_in_flight": r.max_in_flight,
                "hard_limit": r.hard_limit,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "ceiling_capability": Measurement(
                        value="unsupported", unit="", evidence="B")
                },
                notes="runtime exposes no concurrency-ceiling probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        return TaskResult(
            measurements={
                "ceiling_capability": Measurement(
                    value="supported", unit="", evidence="B"),
                "max_in_flight": Measurement(
                    value=raw["max_in_flight"], unit="", evidence="B"),
                "hard_limit": Measurement(
                    value=bool(raw["hard_limit"]), unit="", evidence="B"),
            },
            notes=f"max_in_flight={raw['max_in_flight']} hard_limit={raw['hard_limit']}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
