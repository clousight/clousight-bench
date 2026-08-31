"""TPC-DS benchmark suite plugin (DuckDB local reference).

Registers as the ``tpc-ds`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Drives DuckDB's ``tpcds`` extension (schema/data generation +
the 99 queries) on the ``duckdb-local`` data-warehouse platform. A thin subclass
of :class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcSuite` — the sibling of the
``tpc-h`` suite, sharing the same lifecycle, artifact schema, and digest rules.

The real ``run()`` path needs the optional ``[tpcds]`` extra (``duckdb``); it is
imported lazily so this module loads without it. ``mock_artifacts()`` /
``resolve()`` work with no extra — the recommended CI/offline path.

Correctness: DuckDB's ``tpcds_answers()`` is unusable (no params, defaults to
SF10, errors), so correctness is a *pinned reference* — the normalized digest of
each SF1 query result, captured once against the pinned duckdb+extension version
(``fixtures/reference/sf1_digests.json``, produced by
``scripts/capture_tpcds_reference.py``). This is a deterministic
reproducibility/regression check, NOT an externally-audited TPC answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.suites._duckdb_tpc import DuckDbTpcSuite, result_digest
from clousight_bench.suites._duckdb_tpc import run_query_set as _run_query_set

# Re-exported for scripts/capture_tpcds_reference.py + the suite tests.
__all__ = ["TpcdsSuite", "run_query_set", "result_digest", "_ALL_QUERY_IDS"]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pins the engine + extension + reference-capture that this suite's numbers are
# attributable to. Bump (and re-capture the reference) on any of those changing.
_SUITE_VERSION = "duckdb-1.5.4/tpcds/sf1-ref-v1"

_ALL_QUERY_IDS: tuple[int, ...] = tuple(range(1, 100))  # TPC-DS has 99 queries


def run_query_set(con: Any, query_ids: list[int]) -> list[dict[str, Any]]:
    """Run the TPC-DS query set on *con* (``PRAGMA tpcds(nr)``). Kept module-level
    and extension-bound so ``scripts/capture_tpcds_reference.py`` imports it."""
    return _run_query_set(con, query_ids, extension="tpcds")


class TpcdsSuite(DuckDbTpcSuite):
    """TPC-DS on the duckdb-local reference platform."""

    suite_id = "tpc-ds"
    suite_version = _SUITE_VERSION
    extension = "tpcds"
    dbgen_proc = "dsdgen"
    extra = "tpcds"
    slug = "tpcds"
    all_query_ids = _ALL_QUERY_IDS
    fixtures_dir = _FIXTURES_DIR
