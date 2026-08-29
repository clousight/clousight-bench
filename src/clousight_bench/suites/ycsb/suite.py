"""YCSB benchmark suite plugin (key-value domain).

Registers as the ``ycsb`` suite under the ``clousight_bench.benchmark_suites``
entry-point group. Wraps the recognized upstream **YCSB** tool (Yahoo! Cloud
Serving Benchmark, Apache-2.0) — load phase + run phase — like the SWE-bench
suite wraps the Docker harness. The real ``run()`` path needs the YCSB launcher
(Java >= 11) on ``PATH`` or ``$YCSB_HOME``; ``mock_artifacts()`` / ``resolve()``
need nothing and are the offline / CI path.

The SUT connection is YCSB's own *binding* + endpoint, resolved from the run
``Target`` by the key-value adapters (``ycsb-local`` binding=basic;
``ycsb-endpoint`` binding+endpoint = config-connect to a running service).

YCSB is a performance benchmark: the evaluator reports throughput + tail
latency (environmental). There is no answer-correctness dimension.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
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

# Pins the YCSB distribution the bundled mock fixture reflects.
_SUITE_VERSION = "ycsb-0.17.0"

# The recognized YCSB core workloads (operation mixes A–F).
_CORE_WORKLOADS: tuple[str, ...] = (
    "workloada",
    "workloadb",
    "workloadc",
    "workloadd",
    "workloade",
    "workloadf",
)


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_artifacts(tmp_dir: Path, ycsb_output: str, summary: dict[str, Any]) -> RawArtifacts:
    """Write ycsb_output.txt + summary.json into *tmp_dir* and build the manifest."""
    o_path = tmp_dir / "ycsb_output.txt"
    s_path = tmp_dir / "summary.json"
    o_path.write_text(ycsb_output, encoding="utf-8")
    s_path.write_text(json.dumps(summary), encoding="utf-8")
    manifest: dict[str, dict[str, Any]] = {
        "ycsb_output": {
            "path": "ycsb_output.txt",
            "sha256": _sha256_bytes(o_path.read_bytes()),
            "rows": None,
        },
        "summary": {"path": "summary.json", "sha256": _sha256_bytes(s_path.read_bytes()), "rows": None},
    }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def _ycsb_binary() -> str | None:
    import os  # noqa: PLC0415

    found = shutil.which("ycsb")
    if found:
        return found
    home = os.environ.get("YCSB_HOME", "")
    candidate = os.path.join(home, "bin", "ycsb") if home else ""
    if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return None


def _binding_and_props(target: Target) -> tuple[str, list[str]]:
    """Resolve the YCSB binding + connection `-p key=value` props from *target*.

    ``target.handle`` (the key-value adapter) supplies the default binding
    (basic / redis); ``target.endpoint`` supplies host:port for a networked
    binding. This is the config-connect seam.
    """
    binding = "basic"
    handle = target.handle
    if handle is not None and hasattr(handle, "binding"):
        binding = str(handle.binding())
    props: list[str] = []
    if binding == "redis" and target.endpoint:
        host, _, port = str(target.endpoint).partition(":")
        if host:
            props += ["-p", f"redis.host={host}"]
        if port:
            props += ["-p", f"redis.port={port}"]
    return binding, props


class YcsbSuite(BenchmarkSuite):
    """YCSB on the key-value domain. Wraps the upstream YCSB tool.

    The real path needs the YCSB launcher (Java); ``mock_artifacts()``
    and ``resolve()`` do not.
    """

    suite_id: str = "ycsb"
    suite_version: str = _SUITE_VERSION

    # ------------------------------------------------------------------ resolve
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:  # noqa: ARG002
        """Pick workload + record/operation counts (offline; no tool)."""
        workload = str(cfg.get("workload", "workloada"))
        if workload not in _CORE_WORKLOADS:
            raise ValueError(f"unknown YCSB workload {workload!r}; choose from {_CORE_WORKLOADS}")
        recordcount = int(cfg.get("recordcount", 10000))
        operationcount = int(cfg.get("operationcount", 10000))
        canonical = json.dumps(
            {
                "workload": workload,
                "recordcount": recordcount,
                "operationcount": operationcount,
                "version": self.suite_version,
            },
            sort_keys=True,
        )
        return DatasetHandle(
            version=f"{self.suite_version}/{workload}",
            digest=_sha256_bytes(canonical.encode()),
            payload={
                "workload": workload,
                "recordcount": recordcount,
                "operationcount": operationcount,
            },
        )

    # ------------------------------------------------------------------ prepare
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:  # noqa: ARG002
        """Resolve the YCSB binary + binding (mock → empty EnvHandle)."""
        if target.mock:
            return EnvHandle({"mock": True})
        binary = _ycsb_binary()
        if binary is None:
            raise RuntimeError(
                "the ycsb real run() path needs the YCSB launcher — install YCSB (Java >= 11); "
                "put `ycsb` on PATH or set YCSB_HOME"
            )
        binding, props = _binding_and_props(target)
        return EnvHandle(
            {
                "mock": False,
                "binary": binary,
                "binding": binding,
                "props": props,
                "workload": dataset.payload["workload"],
                "recordcount": dataset.payload["recordcount"],
                "operationcount": dataset.payload["operationcount"],
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:  # noqa: ARG002
        """Run YCSB load + run phases; capture the run-phase output."""
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        p = env.payload
        binary, binding = p["binary"], p["binding"]
        workload_arg = ["-P", f"workloads/{p['workload']}"]
        common = [
            *workload_arg,
            "-p",
            f"recordcount={p['recordcount']}",
            "-p",
            f"operationcount={p['operationcount']}",
            *p["props"],
        ]
        # Load phase (populate the store), then the measured run phase.
        subprocess.run([binary, "load", binding, *common], check=True, capture_output=True, text=True)
        run_proc = subprocess.run(
            [binary, "run", binding, *common], check=True, capture_output=True, text=True
        )
        summary = {
            "workload": p["workload"],
            "binding": binding,
            "recordcount": p["recordcount"],
            "operationcount": p["operationcount"],
            "ycsb_version": self.suite_version,
        }
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-ycsb-art-"))
        return _write_artifacts(tmp_dir, run_proc.stdout, summary)

    # ----------------------------------------------------------------- teardown
    def teardown(self, env: EnvHandle) -> None:  # noqa: ARG002, B027
        """Nothing persistent to release (YCSB manages its own store). No-op."""

    # ------------------------------------------------------------ mock_artifacts
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:  # noqa: ARG002
        """Copy the bundled real-format YCSB output fixture — no tool, no network."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-ycsb-mock-"))
        output = (_FIXTURES_DIR / "mock" / "ycsb_output.txt").read_text(encoding="utf-8")
        summary = json.loads((_FIXTURES_DIR / "mock" / "summary.json").read_text())
        return _write_artifacts(tmp_dir, output, summary)
