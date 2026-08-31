"""Official TPC-H evaluator plugin.

Reads ``queries.json`` (+ optional ``summary.json``) from :class:`RawArtifacts`
and returns namespaced :class:`Measurement` objects under the ``tpc-h.`` prefix.
Pure function — no cloud, no credentials, no duckdb. A thin subclass of
:class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcEvaluator`; the sibling of
``official-tpcds-evaluator``.

Correctness (``tpc-h.queries_passed``) compares each query's normalized
``result_digest`` to the suite's pinned SF1 reference. It is a deterministic
reproducibility/regression check vs a specific pinned engine+extension (duckdb
tpch), NOT an externally-audited TPC answer. Emitted ONLY at scale factor 1 (the
reference is SF1-only). Performance (``tpc-h.geomean_latency_ms`` /
``tpc-h.total_runtime_ms``) is honest, environmental, and never claims an audited
QphH composite. (DuckDB ships ``tpch_answers()`` SF1 answers; the capture script
cross-checks the reference against them informationally — a future
exact-answer-format normalization could upgrade this to verified-answer
correctness.)

All measurements carry ``official=True`` under the ``tpc-h.`` namespace (the
conformance contract). That is a *provenance* flag, not an audit claim —
reproducibility is carried by ``reproducibility_class`` and the audited QphH is
simply not emitted. This matches swe-bench + tpc-ds.
"""

from __future__ import annotations

from pathlib import Path

from clousight_bench.suites._duckdb_tpc import DuckDbTpcEvaluator


class OfficialTpchEvaluator(DuckDbTpcEvaluator):
    """Evaluate a TPC-H run's artifacts into ``tpc-h.`` namespaced measurements."""

    evaluator_id = "official-tpch-evaluator"
    suite_id = "tpc-h"
    extension = "tpch"
    fixtures_dir = Path(__file__).parent / "fixtures"
