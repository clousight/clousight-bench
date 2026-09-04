"""Single source of truth for the installed inventory (benchmarks + adapters).

The inventory is derived from the loaded registries — never hand-maintained.
Both ``csbench list --json`` and the docs generator (``scripts/gen_docs.py``)
call :func:`inventory` so a doc block and the CLI can never disagree about which
benchmarks exist or what status an adapter carries.
"""

from __future__ import annotations

from typing import Any

from clousight_bench.core.registry import load_benchmark_suites, load_domains

#: Schema tag emitted alongside the payload (consumed by ``csbench list --json``).
#: 2.0: native tasks removed (single benchmark rail) — domains carry adapters
#: only; benchmarks are the top-level ``suites`` list.
INVENTORY_SCHEMA = "list/2.0"


def inventory() -> dict[str, Any]:
    """Return the installed inventory as a plain, JSON-ready dict.

    Shape::

        {
          "schema": "list/2.0",
          "suites": [{"suite_id", "suite_version"}],
          "domains": [
            {
              "domain": str,
              "description": str,
              "platforms": [{"platform", "status", "provider", "target_example"}],
            },
            ...
          ]
        }

    Suites, domains and platforms are all sorted deterministically so the output
    is stable across runs (the docs generator relies on this).
    """
    out: dict[str, Any] = {
        "schema": INVENTORY_SCHEMA,
        "suites": [
            {"suite_id": sid, "suite_version": suite.suite_version}
            for sid, suite in sorted(load_benchmark_suites().items())
        ],
        "domains": [],
    }
    for name, pack in sorted(load_domains().items()):
        out["domains"].append(
            {
                "domain": name,
                "description": pack.description,
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
