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

import json
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
from clousight_bench.suites._progress import raise_if_cancelled

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Pins the YCSB distribution the bundled mock fixture reflects.
_SUITE_VERSION = "ycsb-0.17.0"

# The suite times its phases in ns (the unit its trajectory spans use); a
# progress step is milliseconds from the load phase's start.
_NS_PER_MS = 1_000_000.0

# The recognized YCSB core workloads (operation mixes A–F).
_CORE_WORKLOADS: tuple[str, ...] = (
    "workloada",
    "workloadb",
    "workloadc",
    "workloadd",
    "workloade",
    "workloadf",
)


def _write_artifacts(
    tmp_dir: Path,
    ycsb_output: str,
    summary: dict[str, Any],
    spans: list[dict[str, Any]] | None = None,
) -> RawArtifacts:
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
    if spans:
        t_path = tmp_dir / "trajectory.jsonl"
        t_path.write_text("".join(json.dumps(s) + "\n" for s in spans), encoding="utf-8")
        manifest["trajectory"] = {
            "path": "trajectory.jsonl",
            "sha256": _sha256_bytes(t_path.read_bytes()),
            "rows": len(spans),
        }
    return RawArtifacts(dir=tmp_dir, manifest=manifest)


def _validated_reliability(raw: Any) -> dict[str, Any] | None:
    """Validate ``params.reliability`` — a driver-side disruption plan.

    Shape: ``{action: reset|stall, at_s: float, stall_ms?: float}``. A different
    disruption is a different benchmark, so the validated plan folds into the
    dataset digest. ``None``/absent → no disruption (the default clean run).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("params.reliability must be a mapping {action, at_s, stall_ms?}")
    action = str(raw.get("action", ""))
    if action not in ("reset", "stall"):
        raise ValueError(f"reliability.action must be reset|stall, got {action!r}")
    at_s = float(raw.get("at_s", 5.0))
    if at_s < 0:
        raise ValueError(f"reliability.at_s must be >= 0, got {at_s}")
    plan: dict[str, Any] = {"action": action, "at_s": at_s}
    if action == "stall":
        stall_ms = float(raw.get("stall_ms", 1000.0))
        if stall_ms <= 0:
            raise ValueError(f"reliability.stall_ms must be > 0, got {stall_ms}")
        plan["stall_ms"] = stall_ms
    return plan


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
        reliability = _validated_reliability(cfg.get("reliability"))
        canonical_fields: dict[str, Any] = {
            "workload": workload,
            "recordcount": recordcount,
            "operationcount": operationcount,
            "version": self.suite_version,
        }
        if reliability is not None:
            # folded only when present — the clean-run digest stays stable;
            # a disruption plan makes it a different benchmark
            canonical_fields["reliability"] = reliability
        canonical = json.dumps(canonical_fields, sort_keys=True)
        version = f"{self.suite_version}/{workload}"
        if reliability:
            version += f"/disrupt-{reliability['action']}"
        return DatasetHandle(
            version=version,
            digest=_sha256_bytes(canonical.encode()),
            payload={
                "workload": workload,
                "recordcount": recordcount,
                "operationcount": operationcount,
                "reliability": reliability,
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
                "reliability": dataset.payload.get("reliability"),
                "endpoint": str(target.endpoint or ""),
            }
        )

    # ---------------------------------------------------------------------- run
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:
        """Run YCSB load + run phases; capture the run-phase output.

        Progress is reported at phase granularity, which is as fine as this suite
        can honestly go: both phases are one opaque, blocking Java process whose
        stdout is only parseable once it exits, so there is nothing to advance
        through and no safe point to interrupt. Cancel is therefore polled at the
        phase boundaries — in particular right before the MEASURED run phase, so
        a cancel never buys a half-finished measurement.
        """
        if target.mock or env.payload.get("mock"):
            return self.mock_artifacts(dict(env.payload))
        p = env.payload
        progress = driver.progress
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
        from time import time_ns  # noqa: PLC0415

        raise_if_cancelled(progress, "ycsb load phase")
        progress.phase("Load", total=1, unit="phase", reports_progress=False)
        progress.log(f"ycsb load: {p['recordcount']} records into the {binding} binding")
        load_start_ns = time_ns()
        subprocess.run([binary, "load", binding, *common], check=True, capture_output=True, text=True)
        load_end_ns = time_ns()
        # Every step of this run is measured against the load's start.
        progress.step("ycsb.load", 0.0, (load_end_ns - load_start_ns) / _NS_PER_MS)
        progress.advance()

        # Announced BEFORE the disruption plan is armed: `schedule_disruption`
        # starts a timer relative to itself, so nothing may be inserted between
        # arming it and starting the run it is meant to hit.
        raise_if_cancelled(progress, "ycsb run phase")
        progress.phase("Run", total=1, unit="phase", reports_progress=False)
        progress.log(f"ycsb run: {p['operationcount']} operations of {p['workload']}")

        # Driver-side disruption (R5): route the MEASURED phase through the
        # harness's TCP proxy toward the real endpoint and fire the configured
        # disruption mid-run. The load phase stays clean — the disruption targets
        # the measured window only. A different plan is a different benchmark
        # (folded into the dataset digest at resolve()).
        reliability = p.get("reliability")
        proxy = None
        timer = None
        run_props = list(common)
        disruption_meta: dict[str, Any] | None = None
        if reliability:
            if not p.get("endpoint") or binding != "redis":
                # NEVER run a clean benchmark under a disruption-labeled dataset:
                # the digest already says disrupt-<action>, so silently skipping
                # the proxy would record a claim that never happened.
                raise RuntimeError(
                    "params.reliability requires the redis binding and a target endpoint; "
                    f"got binding={binding!r}, endpoint={p.get('endpoint')!r} — "
                    "refusing to run a clean benchmark under a disruption-labeled dataset"
                )
            from clousight_bench.core.disruption import (  # noqa: PLC0415
                DisruptionProxy,
                schedule_disruption,
            )

            host, _, port = str(p["endpoint"]).partition(":")
            proxy = DisruptionProxy(host, int(port or 6379))
            proxy_endpoint = proxy.start()
            phost, _, pport = proxy_endpoint.partition(":")
            # drop the real-endpoint redis.host/redis.port -p pairs, keep everything else
            cleaned: list[str] = []
            skip_next = False
            for i, arg in enumerate(common):
                if skip_next:
                    skip_next = False
                    continue
                if (
                    arg == "-p"
                    and i + 1 < len(common)
                    and (
                        str(common[i + 1]).startswith("redis.host=")
                        or str(common[i + 1]).startswith("redis.port=")
                    )
                ):
                    skip_next = True
                    continue
                cleaned.append(arg)
            run_props = [*cleaned, "-p", f"redis.host={phost}", "-p", f"redis.port={pport}"]
            timer = schedule_disruption(
                proxy,
                action=reliability["action"],
                at_s=float(reliability["at_s"]),
                stall_ms=float(reliability.get("stall_ms", 0.0)),
            )

        run_start_ns = time_ns()
        try:
            run_proc = subprocess.run(
                [binary, "run", binding, *run_props], check=True, capture_output=True, text=True
            )
        finally:
            if proxy is not None:
                # quiesce the timer BEFORE snapshotting: cancel if unfired,
                # join if mid-fire, so the stats read is not torn
                if timer is not None:
                    timer.cancel()
                    timer.join(timeout=5.0)
                stats = proxy.snapshot()
                disruption_meta = {
                    "plan": reliability,
                    "fired": bool(stats.disrupted_at_unix_nano),
                    "connections_total": stats.connections_total,
                    "connections_reset": stats.connections_reset,
                    "stall_windows": stats.stall_windows,
                    "stall_ms_total": stats.stall_ms_total,
                    "disrupted_at_unix_nano": list(stats.disrupted_at_unix_nano),
                    "stall_windows_unix_nano": [list(w) for w in stats.stall_windows_unix_nano],
                }
                proxy.stop()
        run_end_ns = time_ns()
        progress.step(
            "ycsb.run",
            (run_start_ns - load_start_ns) / _NS_PER_MS,
            (run_end_ns - load_start_ns) / _NS_PER_MS,
        )
        progress.advance()
        summary = {
            "workload": p["workload"],
            "binding": binding,
            "recordcount": p["recordcount"],
            "operationcount": p["operationcount"],
            "ycsb_version": self.suite_version,
        }
        if disruption_meta is not None:
            summary["disruption"] = disruption_meta
        from clousight_bench.core.tracing import new_trace_id  # noqa: PLC0415
        from clousight_bench.suites._tpc_official.trace import phase_span  # noqa: PLC0415

        trace_id = getattr(driver, "trace_id", "") or new_trace_id()
        base_attrs = {"csbench.suite_id": "ycsb", "db.system.name": binding}
        spans = [
            phase_span(
                trace_id=trace_id,
                name="ycsb.load",
                start_unix_nano=load_start_ns,
                end_unix_nano=load_end_ns,
                attributes={**base_attrs, "csbench.phase": "load"},
            ),
            phase_span(
                trace_id=trace_id,
                name="ycsb.run",
                start_unix_nano=run_start_ns,
                end_unix_nano=run_end_ns,
                attributes={**base_attrs, "csbench.phase": "run"},
            ),
        ]
        if disruption_meta is not None and disruption_meta["fired"]:
            action = disruption_meta["plan"]["action"]
            disruption_attrs = {
                **base_attrs,
                "csbench.phase": "disruption",
                "csbench.disruption_action": action,
            }
            if action == "stall":
                # the gate-enforced window, clamped to run end (the effective
                # window cannot outlive the measured run)
                windows = [
                    (int(start), min(int(end), run_end_ns))
                    for start, end in disruption_meta["stall_windows_unix_nano"]
                ]
            else:
                windows = [(int(at), int(at)) for at in disruption_meta["disrupted_at_unix_nano"]]
            for start_ns, end_ns in windows:
                spans.append(
                    phase_span(
                        trace_id=trace_id,
                        name=f"ycsb.disruption.{action}",
                        start_unix_nano=start_ns,
                        end_unix_nano=end_ns,
                        attributes=disruption_attrs,
                    )
                )
                progress.step(
                    f"ycsb.disruption.{action}",
                    (start_ns - load_start_ns) / _NS_PER_MS,
                    (end_ns - load_start_ns) / _NS_PER_MS,
                    parent="ycsb.run",
                )
        tmp_dir = Path(tempfile.mkdtemp(prefix="csbench-ycsb-art-"))
        return _write_artifacts(tmp_dir, run_proc.stdout, summary, spans)

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
