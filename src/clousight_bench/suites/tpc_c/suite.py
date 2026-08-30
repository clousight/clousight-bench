"""TPC-C benchmark suite plugin (transactional-db domain, via BenchBase).

Registers as the ``tpc-c`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Wraps the recognized upstream **BenchBase** tool (CMU-DB,
Apache-2.0) — create + load + execute phases — like the ycsb suite wraps YCSB.
The real ``run()`` path needs the BenchBase build (Java) via ``$BENCHBASE_HOME``
or a ``benchbase`` launcher; ``mock_artifacts()`` / ``resolve()`` need nothing
and are the offline / CI path.

The SUT connection is BenchBase's *dbtype* + JDBC endpoint, resolved from the run
``Target`` by the transactional-db adapters (``benchbase-local`` dbtype=sqlite;
``jdbc-endpoint`` dbtype+endpoint = config-connect to a running database).

TPC-C via BenchBase is a performance benchmark: the evaluator reports throughput
/ goodput / latency (environmental). The audited **tpmC** is not claimed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from clousight_bench.core.canonical import sha256_bytes as _sha256_bytes
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    RawArtifacts,
    Target,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pins the BenchBase distribution the bundled mock fixture reflects.
_SUITE_VERSION = "benchbase-2023"


def _write_artifacts(tmp_dir: Path, summary_json: str, meta: dict[str, Any]) -> RawArtifacts:
    """Write summary.json (BenchBase output) + meta.json into *tmp_dir*."""
    s_path = tmp_dir / "summary.json"
    m_path = tmp_dir / "meta.json"
    s_path.write_text(summary_json, encoding="utf-8")
    m_path.write_text(json.dumps(meta), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "summary": {"path": "summary.json", "sha256": _sha256_bytes(s_path.read_bytes()), "rows": None},
        "meta": {"path": "meta.json", "sha256": _sha256_bytes(m_path.read_bytes()), "rows": None},
    }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def _benchbase_launcher() -> str | None:
    found = shutil.which("benchbase")
    if found:
        return found
    home = os.environ.get("BENCHBASE_HOME", "")
    for rel in ("benchbase.jar", "benchbase"):
        candidate = os.path.join(home, rel) if home else ""
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _dbtype_and_endpoint(target: Target) -> tuple[str, str]:
    """Resolve BenchBase dbtype + endpoint from *target* (the config-connect seam)."""
    dbtype = "sqlite"
    handle = target.handle
    if handle is not None and hasattr(handle, "dbtype"):
        dbtype = str(handle.dbtype())
    return dbtype, str(target.endpoint or "")


class TpccSuite(BenchmarkSuite):
    """TPC-C on the transactional-db domain. Wraps the upstream BenchBase tool."""

    suite_id: str = "tpc-c"
    suite_version: str = _SUITE_VERSION

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick scalefactor (warehouses) + terminals + time (offline; no tool)."""
        scalefactor = int(cfg.get("scalefactor", 1))
        terminals = int(cfg.get("terminals", 1))
        time_s = int(cfg.get("time", 60))
        canonical = json.dumps(
            {
                "scalefactor": scalefactor,
                "terminals": terminals,
                "time": time_s,
                "version": self.suite_version,
            },
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/sf{scalefactor}",
            digest=_sha256_bytes(canonical.encode()),
            payload={"scalefactor": scalefactor, "terminals": terminals, "time": time_s},
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Resolve the BenchBase launcher + dbtype/endpoint (mock → empty)."""
        if target.mock:
            return EnvHandle({"mock": True})
        launcher = _benchbase_launcher()
        if launcher is None:
            raise RuntimeError(
                "the tpc-c real run() path needs the BenchBase build — build BenchBase (Java); "
                "put `benchbase` on PATH or set BENCHBASE_HOME"
            )
        dbtype, endpoint = _dbtype_and_endpoint(target)
        return EnvHandle(
            {
                "mock": False,
                "launcher": launcher,
                "dbtype": dbtype,
                "endpoint": endpoint,
                "scalefactor": dataset.payload["scalefactor"],
                "terminals": dataset.payload["terminals"],
                "time": dataset.payload["time"],
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        """Run BenchBase create+load+execute; capture the produced summary.json."""
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        p = env.payload
        work = Path(tempfile.mkdtemp(prefix="csbench-tpcc-"))
        results_dir = work / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        config_path = work / "tpcc_config.xml"
        config_path.write_text(_render_config(p, work), encoding="utf-8")
        cmd = [
            p["launcher"],
            "-b",
            "tpcc",
            "-c",
            str(config_path),
            "--create=true",
            "--load=true",
            "--execute=true",
            "-d",
            str(results_dir),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        summaries = sorted(results_dir.glob("*.summary.json"))
        if not summaries:
            raise RuntimeError(f"BenchBase produced no *.summary.json under {results_dir}")
        summary_json = summaries[-1].read_text(encoding="utf-8")
        meta = {
            "dbtype": p["dbtype"],
            "scalefactor": p["scalefactor"],
            "terminals": p["terminals"],
            "time": p["time"],
            "benchbase_version": self.suite_version,
        }
        art_dir = Path(tempfile.mkdtemp(prefix="csbench-tpcc-art-"))
        shutil.rmtree(work, ignore_errors=True)
        return _write_artifacts(art_dir, summary_json, meta)

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """Nothing persistent to release (embedded sqlite temp is in the work dir). No-op."""

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled real-format BenchBase summary — no tool, no database."""
        art_dir = Path(tempfile.mkdtemp(prefix="csbench-tpcc-mock-"))
        summary_json = (_FIXTURES_DIR / "mock" / "summary.json").read_text(encoding="utf-8")
        meta = json.loads((_FIXTURES_DIR / "mock" / "meta.json").read_text())
        return _write_artifacts(art_dir, summary_json, meta)


def _render_config(p: dict[str, Any], work: Path) -> str:
    """Minimal BenchBase TPC-C config XML (dbtype-driven; endpoint for networked DBs)."""
    dbtype = p["dbtype"]
    if dbtype == "sqlite":
        url = f"jdbc:sqlite:{work / 'tpcc.db'}"
        driver = "org.sqlite.JDBC"
        user = pw = ""
    else:
        host, _, port = str(p["endpoint"]).partition(":")
        url = f"jdbc:{dbtype}://{host}:{port or ''}/benchbase"
        driver = {"postgres": "org.postgresql.Driver", "mysql": "com.mysql.cj.jdbc.Driver"}.get(dbtype, "")
        user = os.environ.get("BENCHBASE_DB_USER", "")
        pw = os.environ.get("BENCHBASE_DB_PASSWORD", "")
    return (
        '<?xml version="1.0"?>\n<parameters>\n'
        f"  <type>{dbtype}</type>\n  <driver>{driver}</driver>\n"
        f"  <url>{url}</url>\n  <username>{user}</username>\n  <password>{pw}</password>\n"
        f"  <scalefactor>{p['scalefactor']}</scalefactor>\n  <terminals>{p['terminals']}</terminals>\n"
        "  <works>\n"
        f"    <work><time>{p['time']}</time><rate>unlimited</rate><weights>45,43,4,4,4</weights></work>\n"
        "  </works>\n</parameters>\n"
    )
