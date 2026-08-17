"""T1.2 session state persistence.

AgentRun (FC-based) has **no native persistent session state**.  State is
managed entirely by the caller (e.g. stored in OSS and re-injected on each
invocation).  This task honestly reports that absence rather than testing the
caller's storage layer.

Evidence layer A: platform architecture documentation.  A stateful platform
that exposes a native session-state API would score positively here; the
negative result is discriminating, not a measurement failure.
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
    "AgentRun (FC-based) has no native persistent session state; state is managed externally by the caller"
)


class StatePersistenceTask(Task):
    task_id = "T1.2"
    title = "Session state persistence"
    evidence_layer = "A"
    task_revision = "3"
    scorer_revision = "3"
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)
    capability_tags = ("reliability/state-persistence",)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id}

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        return {"state_persistence_policy": str(adapter.target.get("state_persistence", "durable"))}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.2 needs an AgentRuntimeAdapter")
        # AgentRun (FC-based) has no native session state; report honestly
        # without exercising the caller's external storage layer.
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
                        "platform has no native persistent session state; "
                        "state is managed externally by the caller "
                        "(platform architecture, not a measurement failure)"
                    ),
                    evidence="A",
                    details={"reason": str(raw.get("reason", _NO_NATIVE_STATE_REASON))},
                )
            ],
            notes="AgentRun has no native session state; caller manages state externally",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
            unsupported=True,
        )
