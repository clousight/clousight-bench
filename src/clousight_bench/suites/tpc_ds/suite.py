"""TPC-DS benchmark suite plugin (DuckDB local reference).

Registers as the ``tpc-ds`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Drives DuckDB's ``tpcds`` extension (schema/data generation +
the 99 queries) on the ``duckdb-local`` data-warehouse platform. A thin subclass
of :class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcSuite` — the sibling of the
``tpc-h`` suite, sharing the same lifecycle, artifact schema, and digest rules.

Two modes (config ``mode``):

* ``reference`` (default) — the single-stream path from ``DuckDbTpcSuite``:
  run the query set once, digest each result. Cheap CI/offline path, scored by
  ``official-tpcds-evaluator``.
* ``official`` — the TPC-DS official sequence (Load → Power → Throughput 1 →
  Data Maintenance 1 → Throughput 2 → Data Maintenance 2 → ACID gate) via
  :mod:`clousight_bench.suites._tpc_official`, emitting ``official.json`` scored
  by ``official-tpcds-qphds-evaluator`` into the official **QphDS@SF** composite.
  Unaudited; differences from the spec encoded honestly:
  no bundled Appendix permutation table → the ordering is ALWAYS the
  deterministic clousight ``generated`` one (recorded as ``ordering_source``);
  the LF_* maintenance functions are replaced by a clousight-generated
  insert+delete round-trip on ``store_sales``; ACID probes atomicity/isolation
  generically (consistency/durability ``n/a``).

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

import json
import tempfile
from pathlib import Path
from typing import Any

from clousight_bench.core.canonical import sha256_bytes
from clousight_bench.core.suite import (
    DatasetHandle,
    DriverContext,
    EnvHandle,
    RawArtifacts,
    Target,
)
from clousight_bench.suites._duckdb_tpc import DuckDbTpcSuite, import_duckdb, result_digest
from clousight_bench.suites._duckdb_tpc import run_query_set as _run_query_set
from clousight_bench.suites._tpc_official import maintenance, phases
from clousight_bench.suites._tpc_official.acid import run_acid_generic
from clousight_bench.suites._tpc_official.streams import GENERATOR_VERSION, generate_orders
from clousight_bench.suites._tpc_official.trace import build_official_ds_spans

# Re-exported for scripts/capture_tpcds_reference.py + the suite tests.
__all__ = ["TpcdsSuite", "run_query_set", "result_digest", "_ALL_QUERY_IDS"]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pins the engine + extension + reference-capture that this suite's numbers are
# attributable to. Bump (and re-capture the reference) on any of those changing.
_SUITE_VERSION = "duckdb-1.5.4/tpcds/sf1-ref-v1"
# Bumped whenever the official phase machine / maintenance set / ordering changes.
_OFFICIAL_VERSION = "official-v1"

_ALL_QUERY_IDS: tuple[int, ...] = tuple(range(1, 100))  # TPC-DS has 99 queries

# TPC-DS official minimum query-stream count (the spec requires at least 4).
_OFFICIAL_MIN_STREAMS = 4


def run_query_set(con: Any, query_ids: list[int]) -> list[dict[str, Any]]:
    """Run the TPC-DS query set on *con* (``PRAGMA tpcds(nr)``). Kept module-level
    and extension-bound so ``scripts/capture_tpcds_reference.py`` imports it."""
    return _run_query_set(con, query_ids, extension="tpcds")


def _write_official(
    tmp_dir: Path, doc: dict[str, Any], spans: list[dict[str, Any]] | None = None
) -> RawArtifacts:
    """Write official.json (+ the reconstructed trajectory) and build the manifest."""
    o_path = tmp_dir / "official.json"
    o_path.write_text(json.dumps(doc), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "official": {"path": "official.json", "sha256": sha256_bytes(o_path.read_bytes()), "rows": None}
    }
    if spans:
        t_path = tmp_dir / "trajectory.jsonl"
        t_path.write_text("".join(json.dumps(s) + "\n" for s in spans), encoding="utf-8")
        manifest["trajectory"] = {
            "path": "trajectory.jsonl",
            "sha256": sha256_bytes(t_path.read_bytes()),
            "rows": len(spans),
        }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def _connect_loaded(duckdb: Any, db_path: str) -> Any:
    con = duckdb.connect(db_path)
    con.execute("LOAD tpcds;")
    return con


class TpcdsSuite(DuckDbTpcSuite):
    """TPC-DS on the duckdb-local reference platform (reference + official modes)."""

    suite_id = "tpc-ds"
    suite_version = _SUITE_VERSION
    extension = "tpcds"
    dbgen_proc = "dsdgen"
    extra = "tpcds"
    slug = "tpcds"
    all_query_ids = _ALL_QUERY_IDS
    fixtures_dir = _FIXTURES_DIR

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:
        if cfg.get("mode", "reference") != "official":
            return super().resolve(cfg, assets)

        sf = float(cfg.get("scale_factor", 1.0))
        streams = int(cfg.get("streams", _OFFICIAL_MIN_STREAMS))
        query_ids = [int(q) for q in cfg.get("query_ids", self.all_query_ids)]
        try:
            ref_sha = sha256_bytes(self._reference_file.read_bytes())
        except OSError:
            ref_sha = "sha256:none"
        canonical = json.dumps(
            {
                "mode": "official",
                "sf": sf,
                "streams": streams,
                "query_ids": sorted(query_ids),
                "query_order": "generated",
                "order": GENERATOR_VERSION,
                "maintenance": "clousight-dm-v1",
                "ref": ref_sha,
                "version": f"{self.suite_version}/{_OFFICIAL_VERSION}",
            },
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/{_OFFICIAL_VERSION}/sf{sf:g}/s{streams}/generated",
            digest=sha256_bytes(canonical.encode()),
            payload={
                "mode": "official",
                "scale_factor": sf,
                "streams": streams,
                "query_ids": query_ids,
            },
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:
        if dataset.payload.get("mode") != "official":
            return super().prepare(target, dataset, driver)
        if target.mock:
            return EnvHandle({"mock": True, "mode": "official"})

        from time import perf_counter  # noqa: PLC0415

        duckdb = import_duckdb(suite_id=self.suite_id, extra=self.extra)
        sf = float(dataset.payload["scale_factor"])
        tmp_dir = tempfile.mkdtemp(prefix=f"csbench-{self.slug}-official-")
        db_path = str(Path(tmp_dir) / f"{self.slug}.duckdb")
        con = duckdb.connect(db_path)
        con.execute("INSTALL tpcds; LOAD tpcds;")
        t = perf_counter()
        con.execute("CALL dsdgen(sf := ?)", [sf])
        load_time_s = perf_counter() - t
        con.close()
        return EnvHandle(
            {
                "mock": False,
                "mode": "official",
                "_tmp_dir": tmp_dir,
                "db_path": db_path,
                "scale_factor": sf,
                "streams": int(dataset.payload["streams"]),
                "query_ids": list(dataset.payload["query_ids"]),
                "load_time_s": load_time_s,
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:
        if env.payload.get("mode") != "official":
            return super().run(target, env, driver)
        if target.mock or env.payload.get("mock"):
            return self.mock_official_artifacts()

        from time import time_ns  # noqa: PLC0415

        duckdb = import_duckdb(suite_id=self.suite_id, extra=self.extra)
        db_path = env.payload["db_path"]
        sf = float(env.payload["scale_factor"])
        streams = int(env.payload["streams"])
        query_ids = [int(q) for q in env.payload.get("query_ids", self.all_query_ids)]
        power_order, throughput_orders = generate_orders(query_ids, num_streams=streams)

        con = _connect_loaded(duckdb, db_path)
        ext_version = con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpcds'"
        ).fetchone()
        anchor_ns = time_ns()
        try:
            doc = phases.run_official_ds(
                con=con,
                open_conn=lambda: _connect_loaded(duckdb, db_path),
                execute_query=lambda c, nr: c.execute(f"PRAGMA tpcds({nr})").fetchall(),
                digest=result_digest,
                run_dm=maintenance.run_dm,
                n_dm_rows=maintenance.maintenance_rows(sf),
                scale_factor=sf,
                power_order=power_order,
                throughput_orders=throughput_orders,
                load_time_s=float(env.payload["load_time_s"]),
                ordering_source=f"clousight-generated/{GENERATOR_VERSION}",
                engine_meta={
                    "duckdb_version": duckdb.__version__,
                    "extension_version": ext_version[0] if ext_version else "unknown",
                },
                acid=lambda c, oc: run_acid_generic(c, oc, table="store_sales", value_column="ss_list_price"),
            )
        finally:
            con.close()

        from clousight_bench.core.tracing import new_trace_id  # noqa: PLC0415

        trace_id = getattr(driver, "trace_id", "") or new_trace_id()
        spans = build_official_ds_spans(
            doc, trace_id=trace_id, anchor_ns=anchor_ns, suite_id=self.suite_id, engine="duckdb"
        )
        art_dir = Path(tempfile.mkdtemp(prefix=f"csbench-{self.slug}-official-art-"))
        return _write_official(art_dir, doc, spans)

    # ------------------------------------------------------------ mock artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:
        """Mock path (``mode: mock``): official fixture for official mode, else reference."""
        if cfg.get("mode", "reference") == "official":
            return self.mock_official_artifacts()
        return super().mock_artifacts(cfg)

    def mock_official_artifacts(self) -> RawArtifacts:
        """Copy the bundled official-mode mock fixture — no duckdb, no network."""
        art_dir = Path(tempfile.mkdtemp(prefix=f"csbench-{self.slug}-official-mock-"))
        doc = json.loads((_FIXTURES_DIR / "mock" / "official.json").read_text())
        spans = [
            json.loads(line)
            for line in (_FIXTURES_DIR / "mock" / "official_trajectory.jsonl").read_text().splitlines()
            if line.strip()
        ]
        return _write_official(art_dir, doc, spans)
