"""T1.2 session state persistence.

Does the runtime keep session state across an interruption + resume? We write
a known state, ask the runtime to resume the session, then read it back. State
that survives = durable; state that vanishes = ephemeral. Both are findings;
CapabilityNotSupported = the runtime offers no state API at all.

Evidence layer C: deterministic, no cloud account needed on local-sim.
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.plugin import ProviderAdapter, Task, TaskOutput
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
    required_permissions = (perm.SESSION_CREATE, perm.SESSION_STATE)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "probe_state": _PROBE_STATE}

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, AgentRuntimeAdapter), "T1.2 needs an AgentRuntimeAdapter"
        session = adapter.create_session()
        try:
            adapter.persist_state(session, _PROBE_STATE)
            resumed = adapter.resume_session(session)
            recovered = adapter.load_state(resumed)
        except CapabilityNotSupported as exc:
            return TaskOutput(
                metrics={"state_capability": "unsupported", "state_persisted": False},
                evidence_layer=self.evidence_layer,
                ok=True,  # "no state API" is a valid, recorded finding
                notes=f"runtime exposes no state persistence: {exc}",
            )
        finally:
            adapter.destroy_session(session)

        persisted = recovered == _PROBE_STATE
        metrics = {
            "state_capability": "supported",
            "state_persisted": persisted,
            "persistence_mode": "durable" if persisted else "ephemeral",
        }
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=True,
            raw={"probe": _PROBE_STATE, "recovered": recovered},
            notes=f"state after resume -> {'durable' if persisted else 'ephemeral'}",
        )
