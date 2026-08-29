"""TPC-H benchmark suite plugin (DuckDB local reference).

Registers as the ``tpc-h`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Drives DuckDB's ``tpch`` extension (schema/data generation +
the 22 queries) on the ``duckdb-local`` data-warehouse platform — the sibling of
the ``tpc-ds`` suite, sharing the same domain, platform, artifact schema, and
digest normalization.

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

import hashlib
import json
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    RawArtifacts,
    Target,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_REFERENCE_FILE = _FIXTURES_DIR / "reference" / "sf1_digests.json"

# Pins the engine + extension + reference-capture that this suite's numbers are
# attributable to. Bump (and re-capture the reference) on any of those changing.
_SUITE_VERSION = "duckdb-1.5.4/tpch/sf1-ref-v1"

_ALL_QUERY_IDS: tuple[int, ...] = tuple(range(1, 23))  # TPC-H has 22 queries


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# Field separator inside a row, and the numeric rounding, are the wire rules of
# the result digest. They MUST stay identical between run() and the reference
# capture script, or every query would read as "failed".
_ROW_SEP = "\x1f"


def _canon_value(v: Any) -> str:
    """Canonical, cross-platform-stable string for one cell.

    Decimals/floats are rounded to 2 dp with a fixed format so a linux/x86 CI run
    and a macOS/arm dev run produce the same digest; None is a sentinel; bytes are
    decoded; everything else is ``str()``.
    """
    if v is None:
        return "\\N"
    if isinstance(v, (Decimal, float)):
        return f"{float(v):.2f}"
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def result_digest(rows: list[tuple[Any, ...]]) -> str:
    """Order-independent, numeric-stable sha256 of a query result set.

    Rows are canonicalized cell-by-cell, joined, then SORTED (so row order never
    affects the digest) and hashed. Shared by ``run()`` and the reference-capture
    script — do not change one without re-capturing the reference fixture.
    """
    row_strs = [_ROW_SEP.join(_canon_value(c) for c in row) for row in rows]
    row_strs.sort()
    return _sha256_bytes("\n".join(row_strs).encode("utf-8"))


def _write_artifacts(tmp_dir: Path, queries: list[dict[str, Any]], summary: dict[str, Any]) -> RawArtifacts:
    """Write queries.json + summary.json into *tmp_dir* and build the manifest."""
    q_path = tmp_dir / "queries.json"
    s_path = tmp_dir / "summary.json"
    q_path.write_text(json.dumps(queries), encoding="utf-8")
    s_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "queries": {
            "path": "queries.json",
            "sha256": _sha256_bytes(q_path.read_bytes()),
            "rows": len(queries),
        },
        "summary": {"path": "summary.json", "sha256": _sha256_bytes(s_path.read_bytes()), "rows": None},
    }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def _import_duckdb() -> Any:
    try:
        import duckdb  # noqa: PLC0415 - lazy so the module imports without [tpch]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "the tpc-h real run() path needs DuckDB — pip install clousight-bench[tpch]"
        ) from exc
    return duckdb


def run_query_set(con: Any, query_ids: list[int]) -> list[dict[str, Any]]:
    """Run each ``PRAGMA tpch(nr)`` on *con*, returning per-query artifact rows.

    Shared by ``run()`` and the reference-capture script so the timing/digest
    procedure is identical.
    """
    from time import perf_counter  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for nr in query_ids:
        t = perf_counter()
        rows = con.execute(f"PRAGMA tpch({nr})").fetchall()
        latency_ms = (perf_counter() - t) * 1000.0
        out.append(
            {
                "query_nr": int(nr),
                "latency_ms": latency_ms,
                "row_count": len(rows),
                "result_digest": result_digest(rows),
            }
        )
    return out


class TpchSuite(BenchmarkSuite):
    """TPC-H on the duckdb-local reference platform.

    Consumes the ``BenchmarkSuite`` ABC. The ``[tpch]`` extra (duckdb) is needed
    only for the real ``run()``/``prepare()`` path; ``mock_artifacts()`` and
    ``resolve()`` do not import duckdb.
    """

    suite_id: str = "tpc-h"
    suite_version: str = _SUITE_VERSION

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick scale factor + query set (offline, no data gen).

        Digest folds sf + sorted query_ids + the reference fixture's own sha, so
        it changes whenever the workload OR the correctness reference changes.
        """
        sf = float(cfg.get("scale_factor", 1.0))
        query_ids = [int(q) for q in cfg.get("query_ids", _ALL_QUERY_IDS)]
        try:
            ref_sha = _sha256_bytes(_REFERENCE_FILE.read_bytes())
        except OSError:
            ref_sha = "sha256:none"
        canonical = json.dumps(
            {"sf": sf, "query_ids": sorted(query_ids), "version": self.suite_version, "ref": ref_sha},
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/sf{sf:g}",
            digest=_sha256_bytes(canonical.encode()),
            payload={"scale_factor": sf, "query_ids": query_ids},
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Generate TPC-H data at the chosen scale factor into a temp DuckDB db.

        Mock target → empty EnvHandle (never touches duckdb).
        """
        if target.mock:
            return EnvHandle({"mock": True})
        duckdb = _import_duckdb()
        sf = float(dataset.payload["scale_factor"])
        tmp_dir = tempfile.mkdtemp(prefix="csbench-tpch-")
        db_path = str(Path(tmp_dir) / "tpch.duckdb")
        con = duckdb.connect(db_path)
        con.execute("INSTALL tpch; LOAD tpch;")
        con.execute("CALL dbgen(sf := ?)", [sf])
        con.close()
        return EnvHandle(
            {
                "mock": False,
                "_tmp_dir": tmp_dir,
                "db_path": db_path,
                "scale_factor": sf,
                "query_ids": list(dataset.payload["query_ids"]),
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        """Run the query set via ``PRAGMA tpch(nr)`` and emit RawArtifacts."""
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        duckdb = _import_duckdb()
        con = duckdb.connect(env.payload["db_path"])
        con.execute("LOAD tpch;")
        query_ids = list(env.payload["query_ids"])
        queries = run_query_set(con, query_ids)
        ext_version = con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpch'"
        ).fetchone()
        con.close()
        summary = {
            "scale_factor": float(env.payload["scale_factor"]),
            "duckdb_version": duckdb.__version__,
            "extension_version": ext_version[0] if ext_version else "unknown",
            "query_count": len(queries),
            "query_ids": query_ids,
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-tpch-art-"))
        return _write_artifacts(tmp_dir, queries, summary)

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:
        """Remove the temp DuckDB db dir (best-effort, never raises)."""
        tmp_dir = env.payload.get("_tmp_dir")
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled mock fixture into a temp dir — no duckdb, no network."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-tpch-mock-"))
        queries = json.loads((_FIXTURES_DIR / "mock" / "queries.json").read_text())
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return _write_artifacts(tmp_dir, queries, summary)
