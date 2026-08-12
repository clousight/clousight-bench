"""Standard resource-usage metric keys (the cost bridge).

A cost dimension is only possible if tasks report usage in a vocabulary the
pricing enricher recognises. This module is the single source of truth for that
vocabulary, so a task and a ``ResultEnricher`` (e.g. the bundled reference
pricing enricher) agree without a hidden contract. Tasks record whatever subset
they can measure; the enricher prices what it can and reports the rest in notes
-- it never invents numbers.
"""

from __future__ import annotations

from typing import Any

# Recognised usage units. A pricing enricher multiplies each by a unit price.
# Two tiers, priced the same way (qty x unit price):
#
# Coarse (convenient, order-of-magnitude):
#   invocations  -- count of platform/tool invocations (dimensionless)
#   vcpu_hours   -- compute time
#   tokens_1k    -- model tokens in thousands
#   gb_month     -- stored/retained data
#
# Billing-grade (match how serverless actually bills; see core.billing):
#   requests     -- billed request count
#   vcpu_seconds -- billed vCPU-seconds (per-invocation minimum + rounding applied)
#   gb_seconds   -- billed memory GB-seconds (same rules)
#   egress_gb    -- outbound network transferred
USAGE_METRIC_KEYS = (
    "invocations",
    "vcpu_hours",
    "tokens_1k",
    "gb_month",
    "requests",
    "vcpu_seconds",
    "gb_seconds",
    "egress_gb",
)


def attach_usage(metrics: dict[str, Any], **usage: float) -> dict[str, Any]:
    """Record usage metrics onto a metrics dict, ignoring None values.

    Raises on an unrecognised key so a typo can't silently escape pricing.
    Validation happens BEFORE any write, so a bad key never leaves the dict
    half-mutated."""
    unknown = [k for k in usage if k not in USAGE_METRIC_KEYS]
    if unknown:
        raise ValueError(f"unknown usage metric(s) {unknown}; known: {USAGE_METRIC_KEYS}")
    for key, value in usage.items():
        if value is not None:
            metrics[key] = value
    return metrics
