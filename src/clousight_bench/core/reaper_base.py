"""BaseResourceReaper: the provider-agnostic ``sweep`` template every cloud reaper shares.

The tag-filter + age-failsafe + dry-run/delete loop is identical across clouds —
only the per-cloud list/delete SDK calls differ — so it lives here once. A
concrete reaper subclasses this, sets its region + SDK client seams in
``__init__`` *before* calling ``super().__init__`` (so ``_default_list_fns`` can
read them), and implements ``_default_list_fns`` / ``_default_delete``. The
list/delete cloud calls stay behind injectable seams so reapers remain
unit-tested account-free. Each seam yields dicts:
``{"kind","id","created_ts","tags"}``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from clousight_bench.core.plugin import ResourceReaper
from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID


class BaseResourceReaper(ResourceReaper):
    """Shared ``sweep`` template for tag-managed cloud resources.

    A subclass supplies the per-cloud listers (``_default_list_fns``) and the
    delete dispatch (``_default_delete``); both are overridable via constructor
    seams for account-free tests.
    """

    provider: str = "abstract"

    def __init__(
        self,
        list_fns: list[Callable[[], list[dict[str, Any]]]] | None = None,
        delete_fn: Callable[[str, str], None] | None = None,  # (kind, id) -> None
        now: Callable[[], float] = time.time,
    ) -> None:
        self._list_fns = list_fns if list_fns is not None else self._default_list_fns()
        self._delete_fn = delete_fn if delete_fn is not None else self._default_delete
        self._now = now

    def sweep(self, *, dry_run: bool, older_than_s: float | None = None) -> list[dict[str, Any]]:
        acted: list[dict[str, Any]] = []
        for list_fn in self._list_fns:
            for res in list_fn():
                if res.get("tags", {}).get(TAG_MANAGED) != "true":
                    continue
                if older_than_s is not None:
                    created_ts = float(res.get("created_ts") or 0.0)
                    # Fail safe: a resource whose creation time is unknown (0.0)
                    # must NOT be age-reaped — it may be in-flight. Only reap it
                    # in an untimed sweep (older_than_s is None), never by age.
                    if created_ts <= 0.0:
                        continue
                    if self._now() - created_ts < older_than_s:
                        continue
                if not dry_run:
                    self._delete_fn(res["kind"], res["id"])
                acted.append(
                    {
                        "kind": res["kind"],
                        "id": res["id"],
                        "run_id": res.get("tags", {}).get(TAG_RUN_ID, "?"),
                    }
                )
        return acted

    def _default_list_fns(self) -> list[Callable[[], list[dict[str, Any]]]]:
        raise NotImplementedError

    def _default_delete(self, kind: str, resource_id: str) -> None:
        raise NotImplementedError
