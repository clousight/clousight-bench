"""Live-run cost guardrail (advisory).

A pre-run notice that steers away from the two habits that dominated the Aug-2026
bill: iterating logic against real cloud, and running tasks in ISOLATED live runs
(each pays a full managed-runtime cold start instead of amortising it across a
warm pool). The hard cap is ``--cost-budget`` (core.cost_budget); this is just
the nudge.
"""

from __future__ import annotations

#: Platforms that never touch paid cloud — no notice for these.
_LOCAL_PLATFORMS = frozenset({"local-sim", "mock", "mock-runtime"})


def is_live_platform(platform: str) -> bool:
    """True when the platform bills real cloud (not a local sim/mock)."""
    p = (platform or "").lower()
    if p in _LOCAL_PLATFORMS:
        return False
    return not ("local" in p or "sim" in p or "mock" in p)


def live_cost_notice(platform: str, *, task_count: int = 1, allow_live: bool = False) -> str | None:
    """A stderr-bound cost notice for a live run, or None when nothing bills.

    ``task_count`` is how many tasks this invocation runs (1 for a single ``run``,
    len(tasks) for a plan/campaign). A single-task live run gets the extra
    batch-it nudge because an isolated run cannot amortise its cold start.
    """
    if not allow_live or not is_live_platform(platform):
        return None
    lines = [
        f"⚠  live run on '{platform}' — real cloud spend. Managed-runtime cold",
        "   starts often dominate a single run's cost.",
        "   • Iterate logic on --platform local-sim; go live only for final numbers.",
    ]
    if task_count <= 1:
        lines += [
            "   • This is a SINGLE-task live run — it pays a full cold start. Batch",
            "     tasks via a run-plan/campaign to amortise it across a warm pool.",
        ]
    lines.append("   • Cap spend with --cost-budget <usd> (auto-aborts on overspend).")
    return "\n".join(lines)
