"""T1.14 idle-timeout config honor + post-promise decay.

``sessionIdleTimeoutSeconds`` is a keep-warm PROMISE: for that long with no
request, the instance must stay hot (so a request inside the window pays no wake
penalty). This task verifies the platform keeps that promise, then charts what
happens afterwards.

Two parts on one session:
  1. Honor: provision with the configured timeout, idle WITHIN the promised
     window (~0.8×), and check the instance is still warm. Warm → honored. Cold
     inside the window → the promise was broken.
  2. Decay: past the window, sweep idle 1min / 3min / 5min and record when the
     instance drops to deep hibernation (seconds wake) then cold recycle (full
     cold start). Breaks on the first full recycle; 5min cap (longer not worth
     the measurement cost). This is "how long it stays cheaply wakeable after the
     promise expires".

Complements T1.5 (undocumented DEFAULT idle window, no configured timeout) by
measuring the CONFIGURED window's behaviour.

Evidence layer B: reproducible method, environment-dependent numbers. On
local-sim the verdict is a deterministic function of
``target.idle_timeout = {honored, configured_s, deep_onset_s, cold_onset_s}``.
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

# A small configured timeout keeps the honor check to ~30s wall-clock: idle a few
# seconds under it (expect warm), then ~15s over it (expect recycled).
IDLE_TIMEOUT_S = 10


class IdleTimeoutHonorTask(Task):
    task_id = "T1.14"
    title = "Idle-timeout config honor"
    evidence_layer = "B"
    task_revision = "1"
    scorer_revision = "1"
    required_permissions = (perm.SESSION_CREATE, perm.TOOL_INVOKE)
    capability_tags = ("reliability/idle-timeout", "cost/scale-to-zero")

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"task_id": self.task_id, "session_idle_timeout_s": IDLE_TIMEOUT_S}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, AgentRuntimeAdapter):
            raise TypeError("T1.14 needs an AgentRuntimeAdapter")
        return adapter.run_data_plane_probe("idle_timeout_honor", {"session_idle_timeout_s": IDLE_TIMEOUT_S})

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        if raw.get("capability") != "supported":
            return TaskResult(
                measurements={
                    "idle_timeout_capability": Measurement(value="unsupported", unit="", evidence="B")
                },
                findings=[
                    Finding(
                        code="agent_runtime.idle_timeout_probe_absent",
                        severity="info",
                        summary="runtime exposes no idle-timeout honor probe",
                        evidence="B",
                        details={"reason": str(raw.get("reason", ""))},
                    )
                ],
                notes="runtime exposes no idle-timeout honor probe",
                task_revision=self.task_revision,
                scorer_revision=self.scorer_revision,
                unsupported=True,
            )
        configured = raw.get("configured_idle_s")
        promise_ms = raw.get("promise_wake_ms")
        honored = bool(raw.get("honored"))
        deep_onset_s = raw.get("deep_onset_s")
        cold_onset_s = raw.get("cold_onset_s")
        decay_capped = bool(raw.get("decay_capped", False))

        findings: list[Finding] = []
        if not honored:
            findings.append(
                Finding(
                    code="agent_runtime.idle_timeout_not_honored",
                    severity="warning",
                    summary=(
                        f"platform broke the configured {configured}s keep-warm promise: the "
                        "instance was already cold/deep-hibernating while still inside the "
                        "promised window (a request within the timeout paid a wake penalty)"
                    ),
                    evidence="B",
                    details={"configured_idle_s": configured, "promise_wake_ms": promise_ms},
                )
            )
        if cold_onset_s is not None:
            findings.append(
                Finding(
                    code="agent_runtime.idle_recycle_after_promise",
                    severity="info",
                    summary=(
                        f"past the {configured}s promise, the instance recycled to full cold "
                        f"after ~{cold_onset_s}s idle (deep-hibernation onset ~{deep_onset_s}s)"
                    ),
                    evidence="B",
                    details={"cold_onset_s": cold_onset_s, "deep_onset_s": deep_onset_s},
                )
            )
        elif decay_capped:
            findings.append(
                Finding(
                    code="agent_runtime.idle_warm_beyond_sweep",
                    severity="info",
                    summary=(
                        f"past the {configured}s promise, the instance never went fully cold "
                        "within the decay sweep — it stays cheaply wakeable longer than measured"
                    ),
                    evidence="B",
                    details={"deep_onset_s": deep_onset_s},
                )
            )

        measurements: dict[str, Measurement] = {
            "idle_timeout_capability": Measurement(value="supported", unit="", evidence="B"),
            "configured_idle_s": Measurement(value=configured, unit="s", evidence="B"),
            "promise_wake_ms": Measurement(value=promise_ms, unit="ms", evidence="B"),
            "idle_timeout_honored": Measurement(value=honored, unit="", evidence="B"),
            # Post-promise decay curve (None if not reached within the sweep).
            "deep_onset_s": Measurement(value=deep_onset_s, unit="s", evidence="B"),
            "cold_onset_s": Measurement(value=cold_onset_s, unit="s", evidence="B"),
            "decay_capped": Measurement(value=decay_capped, unit="", evidence="B"),
            "cold_start_ms": Measurement(value=raw.get("cold_start_ms"), unit="ms", evidence="B"),
        }
        notes = (
            f"configured={configured}s honored={honored} "
            f"(promise-window wake={promise_ms}ms); "
            f"post-promise deep_onset={deep_onset_s}s cold_onset={cold_onset_s}s"
            + (" (capped)" if decay_capped else "")
        )
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=notes,
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
