"""Official TPC-DS evaluator plugin.

Reads ``queries.json`` + ``summary.json`` from :class:`RawArtifacts` and returns
namespaced :class:`Measurement` objects under the ``tpc-ds.`` prefix. Pure
function — no cloud, no credentials, no duckdb.

Registered via the ``clousight_bench.evaluators`` entry-point group as
``official-tpcds-evaluator``.

Correctness (``tpc-ds.queries_passed``) compares each query's normalized
``result_digest`` to the suite's pinned SF1 reference
(``fixtures/reference/sf1_digests.json``). It is a deterministic
reproducibility/regression check against a recognized implementation (DuckDB's
tpcds), NOT an externally-audited TPC answer. It is emitted ONLY at scale factor
1 (the reference is SF1-only). Performance (``tpc-ds.geomean_latency_ms`` /
``tpc-ds.total_runtime_ms``) is honest, environmental, and never claims the
audited QphDS composite.

Provenance note: as the suite's official evaluator, every measurement carries
``official=True`` under the ``tpc-ds.`` namespace (the conformance contract).
That is a *provenance* flag ("emitted by the recognized evaluator"), not an
audit claim — reproducibility is carried by ``reproducibility_class`` and the
audited QphDS is simply not emitted. This matches swe-bench, whose environmental
``cost_per_resolved`` is likewise ``official=True``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import Evaluator, RawArtifacts

_REFERENCE_FILE = Path(__file__).parent / "fixtures" / "reference" / "sf1_digests.json"


def _load_reference() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_REFERENCE_FILE.read_text())
    except Exception:  # noqa: BLE001 - a missing/broken reference just omits correctness
        return {}


class OfficialTpcdsEvaluator(Evaluator):
    """Evaluate a TPC-DS run's artifacts into namespaced measurements.

    This is the official evaluator for the suite, so every measurement is keyed
    ``tpc-ds.`` and carries ``official=True`` (the conformance contract). Honesty
    is carried by ``reproducibility_class`` (``queries_passed`` is
    ``deterministic``, a pinned-reference SF1-only reproducibility check;
    latencies are ``environmental``) and by NOT emitting an audited QphDS
    composite — not by lowering the ``official`` flag.
    """

    evaluator_id = "official-tpcds-evaluator"
    official = True

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        """Return True only for the ``"tpc-ds"`` suite."""
        return suite_id == "tpc-ds"

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Map queries.json + summary.json → ``tpc-ds.*`` measurements.

        A malformed/missing optional artifact omits only the affected dimension
        (never raises), mirroring swe-bench's fail-safe cost metric.
        """
        out: dict[str, Measurement] = {}

        try:
            queries: list[dict[str, Any]] = json.loads(raw.path("queries").read_text())
        except Exception:  # noqa: BLE001 - no queries → nothing to score
            return out
        if not isinstance(queries, list) or not queries:
            return out

        # --- performance (always, environmental) ----------------------------
        latencies: list[float] = []
        for q in queries:
            try:
                latencies.append(float(q["latency_ms"]))
            except (KeyError, TypeError, ValueError):
                latencies = []
                break

        if latencies:
            total = math.fsum(latencies)
            out["tpc-ds.total_runtime_ms"] = Measurement(
                value=total,
                unit="ms",
                reproducibility_class="environmental",
                official=True,
                aggregation="sum",
                sample_count=len(latencies),
            )
            # geometric mean over strictly-positive wall-times only (real
            # perf_counter deltas are always > 0; guarding keeps evaluate()'s
            # never-raise contract against a degenerate/crafted artifact where
            # math.log(0) / math.log(-x) would otherwise raise).
            positive = [x for x in latencies if x > 0]
            if positive:
                log_mean = math.fsum(math.log(x) for x in positive) / len(positive)
                out["tpc-ds.geomean_latency_ms"] = Measurement(
                    value=math.exp(log_mean),
                    unit="ms",
                    reproducibility_class="environmental",
                    official=True,
                    aggregation="geomean",
                    sample_count=len(positive),
                )

        # --- correctness (SF1 only, deterministic) ----------
        try:
            summary: dict[str, Any] = json.loads(raw.path("summary").read_text())
            scale_factor = float(summary.get("scale_factor", 0))
        except Exception:  # noqa: BLE001 - can't confirm SF1 → skip correctness
            return out

        if scale_factor != 1.0:
            return out  # reference is SF1-only; do not assert correctness elsewhere

        reference = _load_reference()
        if not reference:
            return out

        passed = 0
        counted = 0
        for q in queries:
            try:
                nr = str(int(q["query_nr"]))
                digest = q["result_digest"]
            except (KeyError, TypeError, ValueError):
                continue
            ref = reference.get(nr)
            if ref is None:
                continue
            counted += 1
            if digest == ref.get("result_digest"):
                passed += 1

        if counted > 0:
            out["tpc-ds.queries_passed"] = Measurement(
                value=passed / counted,
                unit="ratio",
                reproducibility_class="deterministic",
                official=True,
                sample_count=counted,
                notes="pinned-reference reproducibility vs duckdb tpcds SF1; not an audited TPC answer",
            )

        return out
