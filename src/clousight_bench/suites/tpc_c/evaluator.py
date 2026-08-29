"""Official TPC-C evaluator plugin (BenchBase output).

Parses BenchBase's ``summary.json`` into namespaced ``tpc-c.*``
:class:`Measurement` objects. Pure function — no cloud, no credentials, no tool.
Registered as ``official-tpcc-evaluator``.

TPC-C via BenchBase is a performance benchmark, so every measurement is
``environmental`` and there is NO answer-correctness dimension. As the suite's
official evaluator all measurements carry ``official=True`` (a provenance flag
per the conformance contract — not an audit claim; the audited tpmC is not
emitted). ``supports`` returns True only for the ``"tpc-c"`` suite.
"""

from __future__ import annotations

import json
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts

# Top-level summary.json keys → (measurement key, unit).
_TOP: tuple[tuple[str, str, str], ...] = (
    ("Throughput (requests/second)", "tpc-c.throughput_req_per_sec", "req_per_sec"),
    ("Goodput (requests/second)", "tpc-c.goodput_req_per_sec", "req_per_sec"),
)
# "Latency Distribution" sub-map keys → (measurement key, unit).
_LATENCY: tuple[tuple[str, str, str], ...] = (
    ("99th Percentile Latency (microseconds)", "tpc-c.p99_latency_us", "us"),
    ("Median Latency (microseconds)", "tpc-c.median_latency_us", "us"),
    ("Average Latency (microseconds)", "tpc-c.avg_latency_us", "us"),
)


def _measure(value: Any) -> Measurement | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return Measurement(value=v, unit="", reproducibility_class="environmental", official=True)


class OfficialTpccEvaluator(Evaluator):
    """Evaluate a TPC-C (BenchBase) run's summary into performance measurements."""

    evaluator_id = "official-tpcc-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"tpc-c"`` suite."""
        return suite_id == "tpc-c"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Parse BenchBase ``summary.json`` → ``tpc-c.*`` measurements.

        A key absent from the summary is omitted (fail-safe); a missing/broken
        artifact returns ``{}`` and never raises.
        """
        try:
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
        except Exception:  # noqa: BLE001 - no/broken summary → nothing to score
            return {}
        if not isinstance(summary, dict):
            return {}

        out: dict[str, Measurement] = {}
        for src_key, key, unit in _TOP:
            if src_key in summary:
                m = _measure(summary[src_key])
                if m is not None:
                    out[key] = Measurement(
                        value=m.value, unit=unit, reproducibility_class="environmental", official=True
                    )

        dist = summary.get("Latency Distribution")
        if isinstance(dist, dict):
            for src_key, key, unit in _LATENCY:
                if src_key in dist:
                    m = _measure(dist[src_key])
                    if m is not None:
                        out[key] = Measurement(
                            value=m.value, unit=unit, reproducibility_class="environmental", official=True
                        )
        return out
