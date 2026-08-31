"""TPC-H benchmark suite plugin (DuckDB local reference).

Registers as the ``tpc-h`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Drives DuckDB's ``tpch`` extension (schema/data generation +
the 22 queries) on the ``duckdb-local`` data-warehouse platform — the sibling of
the ``tpc-ds`` suite, a thin subclass of
:class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcSuite` sharing the same
lifecycle, artifact schema, and digest rules.

The real ``run()`` path needs the optional ``[tpch]`` extra (``duckdb``); it is
imported lazily so this module loads without it. ``mock_artifacts()`` /
``resolve()`` work with no extra — the recommended CI/offline path.

Correctness is a *pinned reference* — the normalized digest of each SF1 query
result (``fixtures/reference/sf1_digests.json``, produced by
``scripts/capture_tpch_reference.py``), a deterministic reproducibility/
regression check (SF1-only), NOT an audited TPC result. DuckDB does ship usable
``tpch_answers()`` SF1 answers (unlike TPC-DS); the capture script cross-checks
the reference against them informationally, but exact answer-text-format
normalization (CHAR padding, numeric formatting) is a future upgrade — today the
label is the same pinned-reference reproducibility as TPC-DS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.suites._duckdb_tpc import DuckDbTpcSuite, result_digest
from clousight_bench.suites._duckdb_tpc import run_query_set as _run_query_set

# Re-exported for scripts/capture_tpch_reference.py + the suite tests.
__all__ = ["TpchSuite", "run_query_set", "result_digest", "_ALL_QUERY_IDS"]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pins the engine + extension + reference-capture that this suite's numbers are
# attributable to. Bump (and re-capture the reference) on any of those changing.
_SUITE_VERSION = "duckdb-1.5.4/tpch/sf1-ref-v1"

_ALL_QUERY_IDS: tuple[int, ...] = tuple(range(1, 23))  # TPC-H has 22 queries


def run_query_set(con: Any, query_ids: list[int]) -> list[dict[str, Any]]:
    """Run the TPC-H query set on *con* (``PRAGMA tpch(nr)``). Kept module-level
    and extension-bound so ``scripts/capture_tpch_reference.py`` imports it."""
    return _run_query_set(con, query_ids, extension="tpch")


class TpchSuite(DuckDbTpcSuite):
    """TPC-H on the duckdb-local reference platform."""

    suite_id = "tpc-h"
    suite_version = _SUITE_VERSION
    extension = "tpch"
    dbgen_proc = "dbgen"
    extra = "tpch"
    slug = "tpch"
    all_query_ids = _ALL_QUERY_IDS
    fixtures_dir = _FIXTURES_DIR
