"""T1.2 session state persistence.

Does the runtime keep session state across an interruption + resume? We write
a known state, ask the runtime to resume the session, then read it back. State
that survives = durable; state that vanishes = ephemeral. Both are findings;
CapabilityNotSupported = the runtime offers no state API at all.

Evidence layer C: deterministic, no cloud account needed on local-sim.
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

_PROBE_STATE = {"cursor": 42, "scratch": "benchmark-marker"}


class StatePersistenceTask(Task):
    task_id = "T1.2"
    title = "Session state persistence"
    evidence_layer = "C"
    task_revision = "2"
    scorer_revision = "2"
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "probe_state": _PROBE_STATE}

    def environment_facts(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "state_persistence_policy": str(
                adapter.target.get("state_persistence", "durable")
            )
        }

    def execute(
        self, adapter: ProviderAdapter, params: dict[str, Any]
    ) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.2 needs an AgentRuntimeAdapter")
        session = adapter.create_session()
        try:
            try:
                adapter.persist_state(session, _PROBE_STATE)
                resumed = adapter.resume_session(session)
                recovered = adapter.load_state(resumed)
            except CapabilityNotSupported as exc:
                return ObservationBundle(
                    observations={
                        "capability": "unsupported",
                        "probe": dict(_PROBE_STATE),
                        "reason": str(exc),
                    }
                )
        finally:
            adapter.destroy_session(session)
        return ObservationBundle(
            observations={
                "capability": "supported",
                "probe": dict(_PROBE_STATE),
                "recovered": recovered,
            }
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "state_capability": Measurement(
                        value="unsupported", unit="", evidence="C"
                    ),
                    "state_persisted": Measurement(
                        value=False, unit="", evidence="C"
                    ),
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
                notes="runtime exposes no state persistence",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        persisted = raw.get("recovered") == raw.get("probe")
        mode = "durable" if persisted else "ephemeral"
        findings = (
            []
            if persisted
            else [
                Finding(
                    code="agent_runtime.state_ephemeral",
                    severity="warning",
                    summary="session state did not survive an interruption and resume",
                    evidence="C",
                    details={
                        "probe": raw.get("probe", {}),
                        "recovered": raw.get("recovered"),
                    },
                )
            ]
        )
        return TaskResult(
            measurements={
                "state_capability": Measurement(
                    value="supported", unit="", evidence="C"
                ),
                "state_persisted": Measurement(
                    value=persisted, unit="", evidence="C"
                ),
                "persistence_mode": Measurement(
                    value=mode, unit="", evidence="C"
                ),
            },
            findings=findings,
            notes=f"state after resume -> {'durable' if persisted else 'ephemeral'}",
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
