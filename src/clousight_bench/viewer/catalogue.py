"""What this build can measure, and what it is made of.

Two read-only faces, both computed from the live registry rather than from a
list kept beside it. The rule is the same one the platform catalogue follows:
a hand-maintained inventory is the one that is wrong the week a plugin lands,
and an inventory that is wrong is worse than none, because it is believed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def installed_suites(results_dir: Path) -> list[dict[str, Any]]:
    """Every benchmark this build can run, with its pin, scorers and history.

    ``suite_version`` is the pin — the same benchmark id always means the same
    data — so it is the field a reader checks before comparing two numbers.
    ``runs`` counts what this results directory holds, which is the difference
    between "this benchmark exists" and "we have ever used it".
    """
    from clousight_bench.core.registry import load_benchmark_suites, load_evaluators
    from clousight_bench.viewer.data import list_records
    from clousight_bench.viewer.launch import seen_pairs

    evaluators = load_evaluators()
    seen = seen_pairs(results_dir)
    counts: dict[str, int] = {}
    for record in list_records(results_dir):
        suite_id = str(record.get("suite_id") or "")
        if suite_id:
            counts[suite_id] = counts.get(suite_id, 0) + 1

    out: list[dict[str, Any]] = []
    for suite_id, suite in sorted(load_benchmark_suites().items()):
        scorers = [
            {
                "evaluator_id": str(getattr(ev, "evaluator_id", "")),
                # `official` is the difference between "the suite's canonical
                # numbers" and "a number someone computed": it travels with
                # every measurement and belongs beside the evaluator here too.
                "official": bool(getattr(ev, "official", False)),
            }
            for ev in evaluators
            if ev.supports(suite_id, "")
        ]
        out.append(
            {
                "suite_id": suite_id,
                "suite_version": str(getattr(suite, "suite_version", "")),
                "evaluators": scorers,
                "seen_platforms": seen.get(suite_id, []),
                "runs": counts.get(suite_id, 0),
            }
        )
    return out


#: Entry-point group -> the word this console uses for it. Anything not listed
#: is not surfaced: this is a catalogue of the extension points a reader can
#: act on, not a dump of every group the package declares.
_PLUGIN_GROUPS = {
    "clousight_bench.domains": "domain",
    "clousight_bench.benchmark_suites": "benchmark_suite",
    "clousight_bench.evaluators": "evaluator",
    "clousight_bench.metrics": "metric",
    "clousight_bench.enrichers": "enricher",
    "clousight_bench.judges": "judge",
    "clousight_bench.span_exporters": "span_exporter",
}


def installed_plugins() -> dict[str, Any]:
    """Every registered extension point, by kind, and what provides it.

    Entry points are read WITHOUT loading them: a reader asking "what is
    installed" should get an answer even when one of the plugins is the reason
    something else is broken.
    """
    from importlib.metadata import entry_points

    from clousight_bench import PLUGIN_API_VERSION, __version__

    plugins: list[dict[str, Any]] = []
    for group, kind in sorted(_PLUGIN_GROUPS.items()):
        try:
            found = entry_points(group=group)
        except Exception as exc:  # noqa: BLE001 - a broken environment must still render
            logger.warning("viewer: could not read entry points for %s: %s", group, exc)
            continue
        for ep in sorted(found, key=lambda e: e.name):
            dist = getattr(ep, "dist", None)
            plugins.append(
                {
                    "kind": kind,
                    "name": ep.name,
                    "target": ep.value,
                    "distribution": "" if dist is None else f"{dist.name} {dist.version}",
                }
            )
    return {
        "plugins": plugins,
        "core_version": __version__,
        "plugin_api": PLUGIN_API_VERSION,
    }
