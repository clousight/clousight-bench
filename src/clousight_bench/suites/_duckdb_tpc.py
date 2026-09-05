"""Shared base for the DuckDB-backed TPC suites (TPC-DS / TPC-H).

The two suites are the same benchmark shape on the ``duckdb-local`` data-warehouse
platform — generate data at a scale factor, run ``PRAGMA <ext>(nr)`` per query,
digest each result set, emit ``queries.json`` + ``summary.json`` — differing only
in the DuckDB extension (``tpcds``/``tpch``), the data-gen proc (``dsdgen``/
``dbgen``), the query count, the pinned version, and the ``[extra]``. This module
holds the identical machinery; each suite is a thin subclass setting those knobs.

The digest wire rules (``_canon_value`` / ``result_digest`` / ``write_artifacts``)
are shared verbatim — they MUST stay identical between ``run()`` and the
reference-capture scripts, or every query would read as "failed".
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from clousight_bench.core.canonical import sha256_bytes
from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    Evaluator,
    RawArtifacts,
    Target,
)

# Field separator inside a row, and the numeric rounding, are the wire rules of the
# result digest. They MUST stay identical between run() and the capture scripts.
_ROW_SEP = "\x1f"


def canon_value(v: Any) -> str:
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
    scripts — do not change without re-capturing the reference fixtures.
    """
    row_strs = [_ROW_SEP.join(canon_value(c) for c in row) for row in rows]
    row_strs.sort()
    return sha256_bytes("\n".join(row_strs).encode("utf-8"))


def write_artifacts(tmp_dir: Path, queries: list[dict[str, Any]], summary: dict[str, Any]) -> RawArtifacts:
    """Write queries.json + summary.json into *tmp_dir* and build the manifest."""
    q_path = tmp_dir / "queries.json"
    s_path = tmp_dir / "summary.json"
    q_path.write_text(json.dumps(queries), encoding="utf-8")
    s_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "queries": {
            "path": "queries.json",
            "sha256": sha256_bytes(q_path.read_bytes()),
            "rows": len(queries),
        },
        "summary": {"path": "summary.json", "sha256": sha256_bytes(s_path.read_bytes()), "rows": None},
    }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def import_duckdb(*, suite_id: str, extra: str) -> Any:
    """Lazy-import duckdb, or raise a clear install hint for the suite's extra."""
    try:
        import duckdb  # noqa: PLC0415 - lazy so the module imports without the extra
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"the {suite_id} real run() path needs DuckDB — pip install clousight-bench[{extra}]"
        ) from exc
    return duckdb


def run_query_set(con: Any, query_ids: list[int], *, extension: str) -> list[dict[str, Any]]:
    """Run each ``PRAGMA <extension>(nr)`` on *con*, returning per-query artifact rows.

    Shared by ``run()`` and the reference-capture scripts so the timing/digest
    procedure is identical.
    """
    from time import perf_counter  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for nr in query_ids:
        t = perf_counter()
        rows = con.execute(f"PRAGMA {extension}({nr})").fetchall()
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


class DuckDbTpcSuite(BenchmarkSuite):
    """Base for a DuckDB-backed TPC suite. Subclasses set the class attrs below.

    The ``[<extra>]`` extra (duckdb) is needed only for the real
    ``run()``/``prepare()`` path; ``mock_artifacts()`` and ``resolve()`` do not
    import duckdb.
    """

    # --- knobs a subclass sets -------------------------------------------------
    suite_id: str = "abstract-tpc"
    suite_version: str = "0"
    extension: str = ""  # duckdb extension + PRAGMA name, e.g. "tpcds" / "tpch"
    dbgen_proc: str = ""  # data-gen proc, e.g. "dsdgen" / "dbgen"
    extra: str = ""  # pip extra name, e.g. "tpcds" / "tpch"
    slug: str = ""  # short id for temp dir / db file names, e.g. "tpcds" / "tpch"
    all_query_ids: tuple[int, ...] = ()
    fixtures_dir: Path = Path()

    @property
    def _reference_file(self) -> Path:
        return self.fixtures_dir / "reference" / "sf1_digests.json"

    def _reference_file_for(self, sf: float) -> Path:
        """The pinned reference for *sf* (``sf{sf:g}_digests.json``)."""
        return self.fixtures_dir / "reference" / f"sf{sf:g}_digests.json"

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick scale factor + query set (offline, no data gen).

        Digest folds sf + sorted query_ids + the sf-matched reference fixture's
        own sha, so it changes whenever the workload OR the correctness
        reference changes.
        """
        sf = float(cfg.get("scale_factor", 1.0))
        query_ids = [int(q) for q in cfg.get("query_ids", self.all_query_ids)]
        try:
            ref_sha = sha256_bytes(self._reference_file_for(sf).read_bytes())
        except OSError:
            ref_sha = "sha256:none"
        canonical = json.dumps(
            {"sf": sf, "query_ids": sorted(query_ids), "version": self.suite_version, "ref": ref_sha},
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/sf{sf:g}",
            digest=sha256_bytes(canonical.encode()),
            payload={"scale_factor": sf, "query_ids": query_ids},
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Generate data at the chosen scale factor into a temp DuckDB db.

        Mock target → empty EnvHandle (never touches duckdb).
        """
        if target.mock:
            return EnvHandle({"mock": True})
        duckdb = import_duckdb(suite_id=self.suite_id, extra=self.extra)
        sf = float(dataset.payload["scale_factor"])
        tmp_dir = tempfile.mkdtemp(prefix=f"csbench-{self.slug}-")
        db_path = str(Path(tmp_dir) / f"{self.slug}.duckdb")
        con = duckdb.connect(db_path)
        con.execute(f"INSTALL {self.extension}; LOAD {self.extension};")
        con.execute(f"CALL {self.dbgen_proc}(sf := ?)", [sf])
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
        """Run the query set via ``PRAGMA <extension>(nr)`` and emit RawArtifacts."""
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        duckdb = import_duckdb(suite_id=self.suite_id, extra=self.extra)
        con = duckdb.connect(env.payload["db_path"])
        con.execute(f"LOAD {self.extension};")
        query_ids = list(env.payload["query_ids"])
        queries = run_query_set(con, query_ids, extension=self.extension)
        ext_version = con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name=?", [self.extension]
        ).fetchone()
        con.close()
        summary = {
            "scale_factor": float(env.payload["scale_factor"]),
            "duckdb_version": duckdb.__version__,
            "extension_version": ext_version[0] if ext_version else "unknown",
            "query_count": len(queries),
            "query_ids": query_ids,
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"csbench-{self.slug}-art-"))
        return write_artifacts(tmp_dir, queries, summary)

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:
        """Remove the temp DuckDB db dir (best-effort, never raises)."""
        tmp_dir = env.payload.get("_tmp_dir")
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled mock fixture into a temp dir — no duckdb, no network."""
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"csbench-{self.slug}-mock-"))
        queries = json.loads((self.fixtures_dir / "mock" / "queries.json").read_text())
        summary = json.loads((self.fixtures_dir / "mock" / "summary.json").read_text())
        return write_artifacts(tmp_dir, queries, summary)


class DuckDbTpcEvaluator(Evaluator):
    """Base for a DuckDB-backed TPC official evaluator.

    Subclasses set ``evaluator_id`` + ``suite_id`` (== the ``<suite_id>.``
    namespace + ``supports()``) + ``fixtures_dir``. Every measurement is
    ``official=True`` (conformance contract); honesty rides ``reproducibility_class``
    (``queries_passed`` deterministic, a pinned-reference SF1-only check; latencies
    environmental) and NOT emitting an audited QphDS/QphH composite.
    """

    evaluator_id = "abstract-tpc-evaluator"
    official = True
    suite_id = "abstract-tpc"
    extension = ""  # engine slug for the correctness note, e.g. "tpcds" / "tpch"
    fixtures_dir: Path = Path()

    def supports(self, suite_id: str, product: str) -> bool:  # noqa: ARG002
        return suite_id == self.suite_id

    def _load_reference(self, sf: float = 1.0) -> dict[str, dict[str, Any]]:
        try:
            return json.loads((self.fixtures_dir / "reference" / f"sf{sf:g}_digests.json").read_text())
        except Exception:  # noqa: BLE001 - a missing/broken reference just omits correctness
            return {}

    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Map queries.json + summary.json → ``<suite_id>.*`` measurements.

        A malformed/missing optional artifact omits only the affected dimension
        (never raises), mirroring swe-bench's fail-safe cost metric.
        """
        ns = self.suite_id
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
            out[f"{ns}.total_runtime_ms"] = Measurement(
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
                out[f"{ns}.geomean_latency_ms"] = Measurement(
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

        reference = self._load_reference(scale_factor)
        if not reference:
            return out  # no pinned reference captured for this SF — no correctness claim

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
            all_verified = all(
                bool(reference.get(str(int(q.get("query_nr", -1))), {}).get("verified_official"))
                for q in queries
                if str(q.get("query_nr")) in reference
            )
            if all_verified:
                note = (
                    f"reference verified against the official answer set at capture time "
                    f"(duckdb {self.extension}_answers(), SF{scale_factor:g}); not an audited TPC result"
                )
            else:
                note = (
                    f"pinned-reference reproducibility vs duckdb {self.extension} "
                    f"SF{scale_factor:g}; not an audited TPC answer"
                )
            out[f"{ns}.queries_passed"] = Measurement(
                value=passed / counted,
                unit="ratio",
                reproducibility_class="deterministic",
                official=True,
                sample_count=counted,
                notes=note,
            )

        return out
