"""Run-id resource tagging: the single convention for reconciling cloud spend.

A wired adapter must tag every cloud resource it creates with the run's id, so
that a run which dies before ``teardown`` (SIGKILL, a crashed host, a lost
network) leaves orphans that are *findable* -- and therefore reap-able -- rather
than silent, billing forever. This module owns the tag keys so all four clouds
tag identically; ``csbench sweep`` (via a ``ResourceReaper`` plugin) reads them.
"""
from __future__ import annotations

from collections.abc import Mapping

#: Tag key carrying the run id that created the resource.
TAG_RUN_ID = "clousight-bench:run-id"
#: Tag key marking a resource as created by this harness (safe to reap).
TAG_MANAGED = "clousight-bench:managed"


def run_tags(run_id: str | None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The canonical tag set for a resource created during ``run_id``.

    Always marks the resource managed-by-clousight-bench; stamps the run id when
    known (it is None outside a run). ``extra`` (caller / config tags) is merged
    but never overrides the two reserved keys.
    """
    tags: dict[str, str] = dict(extra or {})
    tags[TAG_MANAGED] = "true"
    if run_id:
        tags[TAG_RUN_ID] = run_id
    return tags
