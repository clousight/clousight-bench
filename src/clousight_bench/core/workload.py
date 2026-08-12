"""WorkloadEngine: cross-language load-generator protocol.

Load generation is deliberately NOT reimplemented in this framework. Mature
tools already exist per product category (YCSB / sysbench for databases,
TPC-DS / terasort for big data, OpenMessaging Benchmark for messaging,
fio / stress-ng for compute) and most are not Python. The framework talks to
them across a *process boundary* so a workload can be written in any language
-- including closed-source commercial plugins distributed as binaries.

Protocol (one directory per workload):

    <workload-dir>/
    ├── manifest.yaml      # name, version, entrypoint, params schema, metrics list
    └── <entrypoint>       # any executable

Contract:
    1. The engine invokes: <entrypoint> --params <json-file>
    2. The workload writes metric events to stdout as JSONL:
         {"type": "metric", "name": "throughput_ops", "value": 1234.5}
         {"type": "metric", "name": "p99_ms", "value": 87.2}
         {"type": "log", "message": "..."}            # optional, forwarded to notes
         {"type": "result", "ok": true}               # final line, required
    3. Exit code 0 + a `result` line = success. Anything else = failure.

manifest.yaml:
    name: ycsb-wrapper
    version: 0.1.0
    entrypoint: ./run.sh          # relative to the manifest
    params:                       # documented inputs (informational)
      workload: {type: string, default: workloada}
    metrics: [throughput_ops, read_p99_ms]
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from clousight_bench.core.sandbox import (
    ResourceLimits,
    posix_rlimit_preexec,
    resolve_within,
)


class WorkloadError(RuntimeError):
    pass


@dataclass
class WorkloadResult:
    ok: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    exit_code: int = 0
    series: dict[str, list] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkloadEngine:
    """Runs one manifest-described workload as a subprocess and parses JSONL."""

    workload_dir: Path

    def __post_init__(self) -> None:
        self.workload_dir = Path(self.workload_dir)
        manifest_path = self.workload_dir / "manifest.yaml"
        if not manifest_path.exists():
            raise WorkloadError(f"no manifest.yaml in {self.workload_dir}")
        self.manifest: dict[str, Any] = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

        from clousight_bench.core.schema_validate import (
            SchemaValidationError,
            validate_against_schema,
        )

        def _fallback(m: Any) -> None:
            for key in ("name", "version", "entrypoint"):
                if key not in m:
                    raise WorkloadError(f"manifest.yaml missing required key {key!r}")

        if not isinstance(self.manifest, dict):
            raise WorkloadError("manifest.yaml must be a mapping")
        try:
            validate_against_schema(self.manifest, "workload-manifest", fallback=_fallback)
        except SchemaValidationError as exc:
            raise WorkloadError(f"manifest.yaml invalid: {exc}") from exc

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    def describe(self) -> dict[str, Any]:
        """Identity folded into config_hash: name + version + declared metrics +
        asset identities (name@version + sha256, never contents)."""
        from clousight_bench.core.assets import load_asset_specs

        return {
            "workload": self.name,
            "workload_version": self.version,
            "declared_metrics": self.manifest.get("metrics", []),
            "assets": [s.identity() for s in load_asset_specs(self.manifest)],
        }

    def resolve_assets(
        self, cache_dir: Path | None = None, allow_hosts: tuple[str, ...] = ()
    ) -> dict[str, str]:
        """Resolve every declared asset to a local path (bundled/remote/private).

        Returns {asset_name: path}. Raises NeedLicense for private assets when no
        licensed resolver is installed -- surfaced before the workload runs.
        ``allow_hosts`` tightens which hosts a remote asset may be fetched from
        (empty = host-unrestricted; https + SSRF guard always apply)."""
        from clousight_bench.core.assets import load_asset_specs, resolve_asset

        resolved: dict[str, str] = {}
        for spec in load_asset_specs(self.manifest):
            path = resolve_asset(
                spec, base_dir=self.workload_dir, cache_dir=cache_dir, allow_hosts=allow_hosts
            )
            resolved[spec.name] = str(path)
        return resolved

    def run(
        self,
        params: dict[str, Any] | None = None,
        timeout_s: int = 3600,
        limits: ResourceLimits | None = None,
    ) -> WorkloadResult:
        entry = (self.workload_dir / str(self.manifest["entrypoint"])).resolve()
        if not entry.exists():
            raise WorkloadError(f"entrypoint {entry} does not exist")

        # Resolve declared assets up front; expose their local paths to the
        # workload under params["assets"] (name -> path).
        payload = dict(params or {})
        assets = self.resolve_assets()
        if assets:
            payload["assets"] = assets

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            params_file = f.name

        try:
            proc = subprocess.run(
                [str(entry), "--params", params_file],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=self.workload_dir,
                preexec_fn=posix_rlimit_preexec(limits or ResourceLimits()),
            )

            metrics: dict[str, Any] = {}
            logs: list[str] = []
            series: dict[str, list] = {}
            artifacts: list[dict[str, Any]] = []
            saw_result = False
            result_ok = False
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logs.append(line)  # tolerate non-protocol noise on stdout
                    continue
                etype = event.get("type")
                if etype == "metric":
                    metrics[str(event["name"])] = event["value"]
                elif etype == "log":
                    logs.append(str(event.get("message", "")))
                elif etype == "sample":
                    name = str(event["series"])
                    series.setdefault(name, []).append([event["t"], event["value"]])
                elif etype == "artifact":
                    rel = str(event["path"])
                    blob = resolve_within(self.workload_dir, rel).read_bytes()
                    artifacts.append(
                        {
                            "kind": str(event.get("kind", "artifact")),
                            "path": rel,
                            "media": str(event.get("media", "application/octet-stream")),
                            "sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
                        }
                    )
                elif etype == "result":
                    saw_result = True
                    result_ok = bool(event.get("ok", False))

            if proc.stderr:
                logs.extend(proc.stderr.strip().splitlines()[-20:])

            ok = proc.returncode == 0 and saw_result and result_ok
            return WorkloadResult(
                ok=ok,
                metrics=metrics,
                logs=logs,
                exit_code=proc.returncode,
                series=series,
                artifacts=artifacts,
            )
        finally:
            try:
                os.unlink(params_file)
            except OSError:
                pass
