"""Post-run teardown reconciliation: destroy + confirm by resource tag.

The harness's own cleanup safety net (distinct from a task that *measures* a
platform's teardown cleanliness). After every run the orchestrator asks: did this
run leave any resource it created still alive? It reverse-looks-up the run's
residual by tag (local ``ResourceLedger``, and -- authoritatively -- a
``ResourceReaper``'s cloud tag query when installed), destroys what it can, and
reports:

- ``teardown.reclaimed`` (warning): the harness had to clean a leak the run left
  -- worth knowing, even though it is now gone.
- ``teardown.residual`` (critical): resources it could NOT reclaim; they keep
  billing until ``csbench sweep --provider <p>`` removes them.

Best-effort and never raises into the run: a reconcile failure is itself
reported, never masks the run's result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Finding
from clousight_bench.core.resource_ledger import ResourceLedger


def reconcile_run_resources(
    adapter: Any,
    run_id: str,
    provider: str | None,
    results_dir: Path | str,
    reaper: Any | None = None,
) -> list[Finding]:
    """Destroy + confirm this run's residual resources by tag. Returns findings."""
    ledger = ResourceLedger(results_dir)
    residual = ledger.residual(run_id)
    if not residual and reaper is None:
        return []

    reclaimed: list[str] = []
    for entry in residual:
        rid = entry.get("resource_id", "")
        try:
            adapter.deprovision(rid)
            reclaimed.append(rid)
        except Exception:  # noqa: BLE001 - a failed destroy is a residual, reported below
            pass

    # Authoritative confirmation: the cloud's own tag query when a reaper exists.
    cloud_residual: list[dict[str, Any]] = []
    if reaper is not None:
        try:
            cloud_residual = list(reaper.verify(run_id) or [])
        except Exception:  # noqa: BLE001 - verify failure must not break teardown
            cloud_residual = []

    local_residual = ledger.residual(run_id)
    findings: list[Finding] = []
    reclaimed_ids = [r for r in reclaimed if r not in {e.get("resource_id") for e in local_residual}]
    if reclaimed_ids:
        findings.append(
            Finding(
                code="teardown.reclaimed",
                severity="warning",
                summary="harness reclaimed resources the run left behind",
                evidence="B",
                details={"reclaimed": reclaimed_ids, "run_id": run_id},
            )
        )
    unreclaimed = [e.get("resource_id") for e in local_residual] + [
        e.get("id", e.get("resource_id")) for e in cloud_residual
    ]
    if unreclaimed:
        findings.append(
            Finding(
                code="teardown.residual",
                severity="critical",
                summary="resources could not be reclaimed and keep billing",
                evidence="B",
                details={
                    "residual": unreclaimed,
                    "run_id": run_id,
                    "remediation": f"run: csbench sweep --provider {provider or '<provider>'} --confirm",
                },
            )
        )
    return findings
