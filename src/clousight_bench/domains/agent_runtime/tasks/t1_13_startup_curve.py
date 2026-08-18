"""T1.13 startup-convergence curve (instance reuse / warm-up).

How does per-call latency decay as the same session is hit repeatedly? Call 1
pays cold start (the platform spins up an instance); if the platform reuses that
instance, later calls drop to a warm steady state. The *shape* of that decay —
how far it falls, how many calls until it converges, and whether reuse is
reliable — is a distinguishing property of the runtime, separate from any single
latency number.

This is deliberately distinct from T1.1 (which times ``create_session``, a
client-local UUID op on managed runtimes) and T1.9 (single-point warm TTFT).
T1.13 measures the *data-plane invoke* convergence curve end-to-end.

``execute`` invokes ONE session ``n_calls`` times back to back and records each
call's wall-clock latency. ``score`` reports the cold start, the 2nd/3rd-call
knee, the warm steady state, the cold/warm speedup, when it converged, and
whether reuse held (no errors, enough warm samples).

Evidence layer B: reproducible method, environment-dependent numbers
(region, load, platform instance policy). On local-sim the curve is a
deterministic function of ``target.startup = {cold_ms, warm_ms}``.
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

# 8 calls: 1 cold + enough warm calls to see convergence and a stable steady state.
N_CALLS = 8


class StartupCurveTask(Task):
    task_id = "T1.13"
    title = "Startup-convergence curve (instance reuse)"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("performance/cold-start", "performance/instance-reuse")

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "n_calls": N_CALLS}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.13 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("startup_curve", {"n_calls": N_CALLS})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "startup_curve_capability": Measurement(value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.startup_curve_probe_absent",
                        severity="info",
                        summary="runtime exposes no startup-curve probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no startup-curve probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        cold = raw.get("cold_start_ms")
        second = raw.get("second_call_ms")
        third = raw.get("third_call_ms")
        warm = raw.get("warm_steady_ms")
        speedup = raw.get("speedup_ratio")
        warmed_after = raw.get("warmed_after_n_calls")
        reliable = bool(raw.get("reuse_reliable"))
        errors = int(raw.get("errors", 0))
        n_calls = int(raw.get("n_calls", 0))

        findings: list[Finding] = []
        if not reliable:
            findings.append(
                Finding(
                    code="agent_runtime.startup_reuse_unreliable",
                    severity="warning",
                    summary=(
                        "instance reuse across same-session calls was unreliable "
                        f"({errors} errors in {n_calls} calls, or too few warm samples); "
                        "warm steady-state numbers should be read with caution"
                    ),
                    evidence="B",
                    details={"errors": errors, "n_calls": n_calls},
                )
            )
        if warmed_after is None:
            findings.append(
                Finding(
                    code="agent_runtime.startup_never_warmed",
                    severity="info",
                    summary="no call dropped into the warm zone; the platform may cold-start every call",
                    evidence="B",
                    details={"cold_start_ms": cold},
                )
            )

        measurements: dict[str, Measurement] = {
            "startup_curve_capability": Measurement(value="supported", unit="", evidence="B"),
            "cold_start_ms": Measurement(value=cold, unit="ms", evidence="B"),
            "second_call_ms": Measurement(value=second, unit="ms", evidence="B"),
            "third_call_ms": Measurement(value=third, unit="ms", evidence="B"),
            "warm_steady_ms": Measurement(value=warm, unit="ms", evidence="B", aggregation="p50"),
            "cold_warm_speedup": Measurement(value=speedup, unit="", evidence="B"),
            "warmed_after_n_calls": Measurement(value=warmed_after, unit="", evidence="B"),
            "reuse_reliable": Measurement(value=reliable, unit="", evidence="B"),
        }
        notes = (
            f"cold={cold}ms → warm_steady={warm}ms "
            f"(speedup={speedup}×, 2nd={second}ms 3rd={third}ms, "
            f"warmed after call {warmed_after}, reliable={reliable}, errors={errors})"
        )
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=notes,
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
