"""Single source of truth for the installed-domain inventory.

The task/adapter inventory is derived from the loaded domain packs — never
hand-maintained. Both ``csbench list --json`` and the docs generator
(``scripts/gen_docs.py``) call :func:`inventory` so a doc block and the CLI can
never disagree about how many tasks exist or what status an adapter carries.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.core.registry import load_domains

#: Schema tag emitted alongside the payload (consumed by ``csbench list --json``).
INVENTORY_SCHEMA = "list/1.0"


def inventory() -> dict[str, Any]:
    """Return the installed-domain inventory as a plain, JSON-ready dict.

    Shape::

        {
          "schema": "list/1.0",
          "domains": [
            {
              "domain": str,
              "description": str,
              "tasks": [{"task_id", "title", "evidence_layer", "capability_tags"}],
              "platforms": [{"platform", "status", "provider", "target_example"}],
            },
            ...
          ]
        }

    Domains, tasks and platforms are all sorted deterministically so the output
    is stable across runs (the docs generator relies on this).
    """
    out: dict[str, Any] = {"schema": INVENTORY_SCHEMA, "domains": []}
    for name, pack in sorted(load_domains().items()):
        out["domains"].append(
            {
                "domain": name,
                "description": pack.description,
                "tasks": [
                    {
                        "task_id": tid,
                        "title": tcls.title,
                        "evidence_layer": tcls.evidence_layer,
                        "capability_tags": list(tcls.capability_tags),
                    }
                    for tid, tcls in sorted(pack.tasks().items())
                ],
                "platforms": [
                    {
                        "platform": pname,
                        "status": acls.status,
                        "provider": acls.provider,
                        "target_example": acls.target_example,
                    }
                    for pname, acls in sorted(pack.adapters().items())
                ],
            }
        )
    return out
