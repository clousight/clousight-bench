"""TPC-H benchmark suite plugin (DuckDB local reference).

Registers as the ``tpc-h`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Drives DuckDB's ``tpch`` extension (schema/data generation +
the 22 queries) on the ``duckdb-local`` data-warehouse platform — the sibling of
the ``tpc-ds`` suite, a thin subclass of
:class:`clousight_bench.suites._duckdb_tpc.DuckDbTpcSuite` sharing the same
lifecycle, artifact schema, and digest rules.

Two modes (config ``mode``):

* ``reference`` (default) — the single-stream path from ``DuckDbTpcSuite``: run the
  query set once, digest each result, emit ``queries.json`` + ``summary.json``. The
  cheap CI/offline path, scored by ``official-tpch-evaluator``.
* ``official`` — the full official pipeline (Load → Power incl. RF1/RF2 →
  multi-stream Throughput → ACID) via :mod:`clousight_bench.suites._tpc_official`,
  emitting ``official.json`` scored by ``official-tpch-qphh-evaluator`` into the
  official ``QphH@Size`` composite. Numbers are unaudited.

The real ``run()`` path needs the optional ``[tpch]`` extra (``duckdb``); it is
imported lazily so this module loads without it. ``mock_artifacts()`` / ``resolve()``
work with no extra — the recommended CI/offline path.

Correctness is a *pinned reference* — the normalized digest of each query
result at a captured scale factor (``fixtures/reference/sf<sf>_digests.json``,
produced by ``scripts/capture_tpch_reference.py``, which verifies every entry
against DuckDB's official ``tpch_answers()``), a deterministic check keyed by
SF, NOT an audited TPC result.
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
from clousight_bench.suites._duckdb_tpc import (
    DuckDbTpcSuite,
    import_duckdb,
    result_digest,
)
from clousight_bench.suites._duckdb_tpc import run_query_set as _run_query_set
from clousight_bench.suites._tpc_official import phases, refresh
from clousight_bench.suites._tpc_official.streams import (
    GENERATOR_VERSION,
    generate_orders,
    official_min_streams,
    resolve_orders,
)
from clousight_bench.suites._tpc_official.trace import build_official_spans

_QUERY_ORDER_SOURCES = ("official", "generated")

# Re-exported for scripts/capture_tpch_reference.py + the suite tests.
__all__ = ["TpchSuite", "run_query_set", "result_digest", "_ALL_QUERY_IDS"]

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_QUERY_ORDER_FILE = _FIXTURES_DIR / "query_order.json"

# Pins the engine + extension + reference-capture that this suite's numbers are
# attributable to. Bump (and re-capture the reference) on any of those changing.
_SUITE_VERSION = "duckdb-1.5.4/tpch/sf1-ref-v1"
# Bumped whenever the official phase machine / refresh set / order table changes.
_OFFICIAL_VERSION = "official-v2"

_ALL_QUERY_IDS: tuple[int, ...] = tuple(range(1, 23))  # TPC-H has 22 queries


def run_query_set(con: Any, query_ids: list[int]) -> list[dict[str, Any]]:
    """Run the TPC-H query set on *con* (``PRAGMA tpch(nr)``). Kept module-level
    and extension-bound so ``scripts/capture_tpch_reference.py`` imports it."""
    return _run_query_set(con, query_ids, extension="tpch")


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
    """Open a new connection to *db_path* with the tpch extension loaded."""
    con = duckdb.connect(db_path)
    con.execute("LOAD tpch;")
    return con


class TpchSuite(DuckDbTpcSuite):
    """TPC-H on the duckdb-local reference platform (reference + official modes)."""

    suite_id = "tpc-h"
    suite_version = _SUITE_VERSION
    extension = "tpch"
    dbgen_proc = "dbgen"
    extra = "tpch"
    slug = "tpch"
    all_query_ids = _ALL_QUERY_IDS
    fixtures_dir = _FIXTURES_DIR

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:
        if cfg.get("mode", "reference") != "official":
            if cfg.get("query_order_file"):
                raise ValueError("query_order_file only applies to mode: official")
            return super().resolve(cfg, assets)

        sf = float(cfg.get("scale_factor", 1.0))
        streams = int(cfg.get("streams", official_min_streams(sf)))
        query_ids = [int(q) for q in cfg.get("query_ids", self.all_query_ids)]
        query_order = str(cfg.get("query_order", "official"))
        if query_order not in _QUERY_ORDER_SOURCES:
            raise ValueError(f"query_order must be one of {_QUERY_ORDER_SOURCES}, got {query_order!r}")
        # Operators can supply the full official Appendix A table themselves
        # (we bundle only streams 0-2 and never fabricate the rest): the file's
        # sha folds into the digest so a different table is a different benchmark.
        order_file = str(cfg.get("query_order_file", "") or "")
        if order_file and query_order != "official":
            raise ValueError("query_order_file only applies to query_order: official")
        # The ordering provenance folded into the digest: the permutation file's
        # sha for official, the generator version for generated.
        if query_order == "official":
            table_path = Path(order_file) if order_file else _QUERY_ORDER_FILE
            try:
                order_prov = sha256_bytes(table_path.read_bytes())
            except OSError as exc:
                if order_file:
                    raise ValueError(f"query_order_file {order_file!r} is not readable: {exc}") from exc
                order_prov = "sha256:none"
        else:
            order_prov = GENERATOR_VERSION
        # No reference sha here: the official evaluator makes no correctness
        # claim (Power runs post-RF1), so the reference cannot affect the result.
        canonical = json.dumps(
            {
                "mode": "official",
                "sf": sf,
                "streams": streams,
                "query_ids": sorted(query_ids),
                "query_order": query_order,
                "order": order_prov,
                "version": f"{self.suite_version}/{_OFFICIAL_VERSION}",
            },
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/{_OFFICIAL_VERSION}/sf{sf:g}/s{streams}/{query_order}",
            digest=sha256_bytes(canonical.encode()),
            payload={
                "mode": "official",
                "scale_factor": sf,
                "streams": streams,
                "query_ids": query_ids,
                "query_order": query_order,
                "query_order_file": order_file,
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
        con.execute("INSTALL tpch; LOAD tpch;")
        t = perf_counter()
        con.execute("CALL dbgen(sf := ?)", [sf])
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
                "query_order": dataset.payload.get("query_order", "official"),
                "query_order_file": dataset.payload.get("query_order_file", ""),
                "load_time_s": load_time_s,
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:
        if env.payload.get("mode") != "official":
            return super().run(target, env, driver)
        if target.mock or env.payload.get("mock"):
            return self.mock_official_artifacts()

        duckdb = import_duckdb(suite_id=self.suite_id, extra=self.extra)
        db_path = env.payload["db_path"]
        sf = float(env.payload["scale_factor"])
        streams = int(env.payload["streams"])
        query_ids = [int(q) for q in env.payload.get("query_ids", self.all_query_ids)]
        query_order = env.payload.get("query_order", "official")
        if query_order == "generated":
            power_order, throughput_orders = generate_orders(query_ids, num_streams=streams)
            ordering_source = f"clousight-generated/{GENERATOR_VERSION}"
        else:
            order_file = str(env.payload.get("query_order_file", "") or "")
            table_path = Path(order_file) if order_file else _QUERY_ORDER_FILE
            table = json.loads(table_path.read_text())
            power_order, throughput_orders = resolve_orders(table, num_streams=streams)
            ordering_source = "official-appendix-a/operator-supplied" if order_file else "official-appendix-a"

        from time import time_ns  # noqa: PLC0415

        con = _connect_loaded(duckdb, db_path)
        ext_version = con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='tpch'"
        ).fetchone()
        anchor_ns = time_ns()
        try:
            doc = phases.run_official(
                con=con,
                open_conn=lambda: _connect_loaded(duckdb, db_path),
                execute_query=lambda c, nr: c.execute(f"PRAGMA tpch({nr})").fetchall(),
                digest=result_digest,
                rf1=refresh.rf1,
                rf2=refresh.rf2,
                n_refresh=refresh.refresh_rows(sf),
                scale_factor=sf,
                power_order=power_order,
                throughput_orders=throughput_orders,
                load_time_s=float(env.payload["load_time_s"]),
                ordering_source=ordering_source,
                engine_meta={
                    "duckdb_version": duckdb.__version__,
                    "extension_version": ext_version[0] if ext_version else "unknown",
                },
            )
        finally:
            con.close()

        from clousight_bench.core.tracing import new_trace_id  # noqa: PLC0415

        trace_id = getattr(driver, "trace_id", "") or new_trace_id()
        spans = build_official_spans(
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
