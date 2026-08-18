"""RestrictedReaper — the controller's self-destruct cleanup.

Deletes, in a fixed order, everything the run created: all live runtimes first
(reverse-looked-up from the ResourceLedger), then the NAT/EIP/SNAT, then the
controller's own ECS instance LAST (so it stays alive long enough to finish the
earlier deletes). Every delete is best-effort — an error is collected, not
raised, so one failure never strands the rest.

Uses a RESTRICTED delete role (this run's resources only), never the MAIN account.
"""

from __future__ import annotations

from collections.abc import Callable

from clousight_bench.core.resource_ledger import ResourceLedger


def live_runtimes_from_ledger(ledger: ResourceLedger) -> list[str]:
    """Runtime resource ids that were created and not since deleted."""
    created: list[str] = []
    deleted: set[str] = set()
    for e in ledger._events():  # noqa: SLF001 — intentional reverse-lookup over the append log
        if e.get("event") == "created" and e.get("kind") == "runtime":
            created.append(str(e.get("resource_id")))
        elif e.get("event") == "deleted":
            deleted.add(str(e.get("resource_id")))
    return [rid for rid in created if rid not in deleted]


class RestrictedReaper:
    def __init__(
        self,
        live_runtimes: Callable[[], list[str]],
        delete_runtime: Callable[[str], None],
        delete_nat: Callable[[], None],
        delete_self: Callable[[str], None],
        self_instance_id: str,
    ) -> None:
        self._live_runtimes = live_runtimes
        self._delete_runtime = delete_runtime
        self._delete_nat = delete_nat
        self._delete_self = delete_self
        self._self_instance_id = self_instance_id

    def reap(self) -> list[str]:
        """Delete runtime(s) → NAT → self (last). Returns collected error strings."""
        errors: list[str] = []
        for rid in self._live_runtimes():
            try:
                self._delete_runtime(rid)
            except Exception as exc:  # noqa: BLE001 — best-effort
                errors.append(f"runtime {rid}: {exc}")
        try:
            self._delete_nat()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"nat: {exc}")
        # Self LAST — the instance must outlive the deletes above.
        try:
            self._delete_self(self._self_instance_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"self {self._self_instance_id}: {exc}")
        return errors
