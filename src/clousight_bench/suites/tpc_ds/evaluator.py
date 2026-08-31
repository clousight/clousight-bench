"""Official TPC-DS evaluator plugin.

Reads ``queries.json`` (+ optional ``summary.json``) from :class:`RawArtifacts`
and returns namespaced :class:`Measurement` objects under the ``tpc-ds.`` prefix.
Pure function — no cloud, no credentials, no duckdb. A thin subclass of
:class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcEvaluator`; the sibling of
``official-tpch-evaluator``.

Correctness (``tpc-ds.queries_passed``) compares each query's normalized
``result_digest`` to the suite's pinned SF1 reference
(``fixtures/reference/sf1_digests.json``). It is a deterministic
reproducibility/regression check vs a specific pinned engine+extension (duckdb
tpcds), NOT an externally-audited TPC answer. It is emitted ONLY at scale factor
1 (the reference is SF1-only). Performance (``tpc-ds.geomean_latency_ms`` /
``tpc-ds.total_runtime_ms``) is honest, environmental, and never claims the
audited QphDS composite.

All measurements carry ``official=True`` under the ``tpc-ds.`` namespace (the
conformance contract). That is a *provenance* flag ("emitted by the recognized
evaluator"), not an audit claim — reproducibility is carried by
``reproducibility_class`` and the audited QphDS is simply not emitted. This
matches swe-bench, whose environmental ``cost_per_resolved`` is likewise
``official=True``.
"""

from __future__ import annotations

from pathlib import Path

from clousight_bench.suites._duckdb_tpc import DuckDbTpcEvaluator


class OfficialTpcdsEvaluator(DuckDbTpcEvaluator):
    """Evaluate a TPC-DS run's artifacts into ``tpc-ds.`` namespaced measurements."""

    evaluator_id = "official-tpcds-evaluator"
    suite_id = "tpc-ds"
    extension = "tpcds"
    fixtures_dir = Path(__file__).parent / "fixtures"
