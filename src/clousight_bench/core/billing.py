"""Serverless billing model: turn raw compute usage into BILLED usage.

Cloud serverless runtimes (Aliyun AgentRun / AWS Lambda-style) do not bill
wall-clock seconds directly: each invocation is billed for at least a minimum
duration and rounded up to a granularity, then charged per vCPU-second and per
GB-second. A cost estimate that ignores these rules mis-counts -- badly for the
short, bursty calls a benchmark makes (a 3 ms call billed at a 100 ms minimum is
33x off). This module applies the rules so the modeled cost tracks the real bill
without waiting on the billing system.

It is pure and provider-agnostic: the concrete numbers (minimum duration,
rounding granularity, vCPU, memory) come from the rate card and the run config,
never hard-coded here. It converts raw per-invocation durations into the
billing-grade usage units the pricing enricher prices
(:data:`core.usage.USAGE_METRIC_KEYS`).
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ComputeBillingRule:
    """Per-invocation billing rules from a provider's rate card.

    ``min_ms``: minimum billable duration charged per invocation, however short
    the call actually was. ``rounding_ms``: each invocation's duration is rounded
    UP to this granularity. Defaults bill the exact duration (no minimum, 1 ms
    granularity), so a caller that does not know the rules is not silently wrong
    in a *hidden* way -- it just gets un-rounded seconds."""

    min_ms: float = 0.0
    rounding_ms: float = 1.0

    def billed_ms(self, duration_ms: float) -> float:
        """The billable duration for one invocation of ``duration_ms``."""
        if duration_ms < 0:
            raise ValueError(f"duration_ms must be >= 0, got {duration_ms!r}")
        billed = max(float(duration_ms), float(self.min_ms))
        if self.rounding_ms and self.rounding_ms > 0:
            billed = math.ceil(billed / self.rounding_ms) * self.rounding_ms
        return billed


def billed_compute(
    durations_ms: Iterable[float],
    *,
    vcpu: float,
    memory_gb: float,
    rule: ComputeBillingRule | None = None,
) -> dict[str, float]:
    """Billing-grade compute usage from raw per-invocation durations.

    Applies ``rule`` per invocation (minimum + rounding), sums the billed time,
    and scales by the configured vCPU and memory. Returns usage keyed by the
    billing-grade names in the usage vocabulary, ready for ``attach_usage``:
    ``requests``, ``vcpu_seconds``, ``gb_seconds``. Rounding is per-invocation --
    never on the aggregate -- because that is how the bill is computed."""
    rule = rule or ComputeBillingRule()
    durations = list(durations_ms)
    billed_seconds = sum(rule.billed_ms(d) for d in durations) / 1000.0
    return {
        "requests": float(len(durations)),
        "vcpu_seconds": round(billed_seconds * float(vcpu), 6),
        "gb_seconds": round(billed_seconds * float(memory_gb), 6),
    }
