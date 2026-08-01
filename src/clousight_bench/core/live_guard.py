"""Live-run guard: the cost safety-belt for real-cloud execution.

A benchmark whose numbers come from a REAL cloud (``execution_mode == "live"``)
drives real traffic -- it spends real money and can trip a provider's quota /
abuse controls. Load / soak / concurrency dimensions especially. So a live run
must not provision anything unless the operator explicitly acknowledged the
cost, either per-invocation (``--allow-live`` / ``execute(allow_live=True)``) or
via the ``CSBENCH_ALLOW_LIVE`` environment escape hatch.

Simulated runs (local-sim, any cloud adapter in ``mode: mock``) are never gated:
they touch no account and cost nothing.

This module is pure and side-effect free; the orchestrator turns a ``blocked``
decision into an ``invalid`` record with a ``live.unconfirmed`` finding, BEFORE
SETUP, so nothing is ever provisioned.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Environment escape hatch: any truthy value acknowledges live-run cost.
ENV_ALLOW_LIVE = "CSBENCH_ALLOW_LIVE"

_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in _TRUTHY


@dataclass
class LiveDecision:
    """Whether this run is live and, if so, whether its cost was acknowledged."""

    is_live: bool
    acknowledged: bool
    limits: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """A live run with no acknowledgement must not provision."""
        return self.is_live and not self.acknowledged


def live_decision(
    execution_mode: str,
    target: Mapping[str, Any],
    allow_live: bool,
    env: Mapping[str, str] | None = None,
) -> LiveDecision:
    """Decide whether a run may proceed given its execution mode + acknowledgement.

    ``execution_mode`` comes from ``adapter.execution_mode()``; only ``"live"``
    is gated. Acknowledgement is ``allow_live`` OR a truthy ``CSBENCH_ALLOW_LIVE``.
    Optional ``target['live_limits']`` (max_concurrency / max_duration_s /
    max_requests) is carried through for a wired transport to enforce and for the
    record to state what bound the operator asked for.
    """
    env = os.environ if env is None else env
    is_live = execution_mode == "live"
    acknowledged = bool(allow_live) or _truthy(env.get(ENV_ALLOW_LIVE, ""))
    limits = dict(target.get("live_limits") or {})
    return LiveDecision(is_live=is_live, acknowledged=acknowledged, limits=limits)
