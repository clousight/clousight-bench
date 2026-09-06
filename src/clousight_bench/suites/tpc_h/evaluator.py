"""Official TPC-H evaluator plugin.

Reads ``queries.json`` (+ optional ``summary.json``) from :class:`RawArtifacts`
and returns namespaced :class:`Measurement` objects under the ``tpc-h.`` prefix.
Pure function — no cloud, no credentials, no duckdb. A thin subclass of
:class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcEvaluator`; the sibling of
``official-tpcds-evaluator``.

Correctness (``tpc-h.queries_passed``) compares each query's normalized
``result_digest`` to the suite's SF-keyed pinned reference
(``reference/sf{sf:g}_digests.json``, shipped at SF 1 / 0.1 / 0.01); at a scale
factor without a shipped reference, correctness is omitted. Each reference is
captured by ``scripts/capture_tpch_reference.py``, which verifies every entry
against DuckDB's official ``tpch_answers()`` at capture time and stamps
``verified_official`` — when every compared entry carries it, the measurement's
notes say so. Still NOT an externally-audited TPC answer. Performance
(``tpc-h.geomean_latency_ms`` / ``tpc-h.total_runtime_ms``) is honest,
environmental, and never claims an audited QphH composite.

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
