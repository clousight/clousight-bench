"""Official YCSB evaluator plugin.

Parses the upstream YCSB run-phase output (``ycsb_output.txt``) into namespaced
``ycsb.*`` :class:`Measurement` objects. Pure function — no cloud, no
credentials, no tool. Registered as ``official-ycsb-evaluator``.

YCSB is a performance benchmark, so every measurement is ``environmental`` and
there is NO answer-correctness dimension. As the suite's official evaluator all
measurements carry ``official=True`` (a provenance flag per the conformance
contract — not an audit claim; drift is carried by ``reproducibility_class``).
"""

from __future__ import annotations

import re

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts

# (measurement key, unit, regex over the standard YCSB output lines).
_METRICS: tuple[tuple[str, str, str], ...] = (
    ("ycsb.overall_runtime_ms", "ms", r"\[OVERALL\], RunTime\(ms\), ([\d.]+)"),
    ("ycsb.throughput_ops", "ops_per_sec", r"\[OVERALL\], Throughput\(ops/sec\), ([\d.]+)"),
    ("ycsb.read_p99_us", "us", r"\[READ\], 99thPercentileLatency\(us\), ([\d.]+)"),
    ("ycsb.update_p99_us", "us", r"\[UPDATE\], 99thPercentileLatency\(us\), ([\d.]+)"),
)


class OfficialYcsbEvaluator(Evaluator):
    """Evaluate a YCSB run's output into namespaced performance measurements."""

    evaluator_id = "official-ycsb-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"ycsb"`` suite."""
        return suite_id == "ycsb"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Parse ``ycsb_output.txt`` → ``ycsb.*`` measurements.

        A metric absent from the output is omitted (fail-safe); a missing/broken
        artifact returns ``{}`` and never raises.
        """
        try:
            text = raw.path("ycsb_output").read_text()
        except Exception:  # noqa: BLE001 - no output → nothing to score
            return {}

        out: dict[str, Measurement] = {}
        for key, unit, pattern in _METRICS:
            m = re.search(pattern, text)
            if not m:
                continue
            try:
                value = float(m.group(1))
            except (TypeError, ValueError):
                continue
            out[key] = Measurement(
                value=value,
                unit=unit,
                reproducibility_class="environmental",
                official=True,
            )
        return out
