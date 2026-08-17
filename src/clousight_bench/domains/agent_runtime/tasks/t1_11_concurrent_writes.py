"""T1.11 concurrent state writes.

AgentRun (FC-based) has **no native persistent session state**; concurrent
write safety cannot be measured because there is no shared state store owned
by the platform.  This task honestly reports that absence.

Evidence layer A: platform architecture documentation.  A stateful platform
that exposes a native session-state API would allow measuring write-safety here;
the negative result is discriminating, not a measurement failure.
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

_NO_NATIVE_STATE_REASON = (
    "AgentRun (FC-based) has no native concurrent session state; state is managed externally by the caller"
)


class ConcurrentWritesTask(Task):
    task_id = "T1.11"
    title = "Concurrent state writes"
    evidence_layer = "A"
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)
    capability_tags = ("reliability/concurrent-writes",)
    task_revision = "2"
    scorer_revision = "2"

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
        # AgentRun (FC-based) has no native session state; report honestly
        # without simulating concurrent writes on the caller's storage layer.
        return ObservationBundle(
            observations={
                "capability": "unsupported",
                "reason": _NO_NATIVE_STATE_REASON,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        return TaskResult(
            measurements={
                "state_capability": Measurement(value="unsupported", unit="", evidence="A"),
            },
            findings=[
                Finding(
                    code="agent_runtime.no_native_session_state",
                    severity="info",
                    summary=(
                        "platform has no native concurrent session state; "
                        "concurrent-write safety cannot be measured "
                        "(platform architecture, not a measurement failure)"
                    ),
                    evidence="A",
                    details={"reason": str(raw.get("reason", _NO_NATIVE_STATE_REASON))},
                )
            ],
            notes="AgentRun has no native session state; concurrent write safety not measurable",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
            unsupported=True,
        )
