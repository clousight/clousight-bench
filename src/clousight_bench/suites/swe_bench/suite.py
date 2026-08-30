"""SWE-bench Verified benchmark suite plugin.

Registers as the ``swe-bench`` suite under the
``clousight_bench.benchmark_suites`` entry-point group.

The real ``run()`` path (Docker + upstream harness) requires the optional
``[swebench]`` extra (``pip install clousight-bench[swebench]``).  All other
paths — ``mock_artifacts()``, ``resolve()``, ``prepare()`` — work without it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
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

# Pinned HuggingFace revision for the SWE-bench Verified split used by this plugin.
# Real main commit of princeton-nlp/SWE-bench_Verified, verified 2026-08-25 via
# https://huggingface.co/api/datasets/princeton-nlp/SWE-bench_Verified/refs
_HF_REVISION = "princeton-nlp/SWE-bench_Verified@c104f840cc67f8b6eec6f759ebc8b2693d585d4a"

# Model name written into each predictions.jsonl line and embedded in the harness report filename.
# Default label for the MockAgent smoke modes (gold/empty); real runs use _MODEL_NAMES.
_MODEL_NAME = "csbench-mock-agent"

# Mode-derived artifact labels for the real SUT path — a real run must never be
# labeled as the mock agent.
_MODEL_NAMES = {"oracle": "csbench-oracle-agent", "llm": "csbench-qwen-agent"}


def _model_name(agent_kind: str) -> str:
    """Artifact label for *agent_kind*: mode-derived for oracle/llm, mock default otherwise."""
    return _MODEL_NAMES.get(agent_kind, _MODEL_NAME)


def _normalize_upstream_report(report: dict[str, Any], instance_ids: list[str]) -> dict[str, Any]:
    """Convert a swebench >= 3.0 schema_version-2 report into the suite's canonical shape.

    The canonical shape is::

        {"per_instance": {iid: {"resolved": bool}}, "resolved": int, "total": int}

    Raises ``RuntimeError`` if the report is missing the expected top-level keys, so that
    a pin upgrade or schema drift fails loudly rather than silently miscounting.
    """
    missing = {"resolved_instances", "total_instances", "resolved_ids"} - report.keys()
    if missing:
        raise RuntimeError(
            f"unrecognized swebench report shape — missing key(s) {sorted(missing)}; "
            f"got top-level keys {sorted(report)[:20]}. Pin a supported swebench version."
        )
    resolved_ids = set(report["resolved_ids"])
    per_instance = {iid: {"resolved": iid in resolved_ids} for iid in instance_ids}
    return {
        "per_instance": per_instance,
        "resolved": int(report["resolved_instances"]),
        "total": int(report["total_instances"]),
    }


def _sha256_file(p: Path) -> str:
    return _sha256_bytes(p.read_bytes())


def _count_jsonl_rows(p: Path) -> int | None:
    """Return the number of newline-delimited JSON objects in a .jsonl file."""
    try:
        return sum(1 for line in p.read_text().splitlines() if line.strip())
    except Exception:
        return None


def _count_json_rows(p: Path) -> int | None:
    """Return the number of top-level list elements for a JSON array file."""
    try:
        data = json.loads(p.read_text())
        if isinstance(data, list):
            return len(data)
        return None
    except Exception:
        return None


class SweBenchSuite(BenchmarkSuite):
    """SWE-bench Verified suite plugin.

    Consumes the Task-1 ``BenchmarkSuite`` ABC.  The ``[swebench]`` optional
    extra is required only for the real Docker-backed ``run()`` path.
    """

    # --- suite identity / dataset binding -----------------------------------
    # These are class attributes so a variant (e.g. SWE-bench Lite / Multimodal)
    # is a thin subclass that overrides them — the run/prepare/mock logic below
    # reads them via ``self``/``cls`` and never hardcodes the Verified split.
    suite_id: str = "swe-bench"
    suite_version: str = _HF_REVISION  # "<hf-dataset>@<commit>" — provenance pin
    fixtures_dir: Path = _FIXTURES_DIR
    dataset_name: str = "princeton-nlp/SWE-bench_Verified"  # harness --dataset_name
    split: str = "test"

    # Parsed instances_full.json, cached on the class after the first read.
    # NOTE: subclasses MUST redeclare this attribute so each variant caches its
    # OWN fixtures (a subclass that omits it would read the base class's cache
    # via the MRO and load the wrong instance rows).
    _instances_full_cache: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # instance fixture access
    # ------------------------------------------------------------------

    def _load_instance(self, iid: str) -> dict[str, Any]:
        """Return the real dataset row for *iid* from ``fixtures/instances_full.json``.

        Each row carries the 6 fields ``instance_id``, ``repo``, ``base_commit``,
        ``problem_statement``, ``hints_text``, ``patch`` with the REAL SWE-bench
        Verified values (``patch`` is the gold patch).  Raises ``KeyError`` listing
        the sorted available ids when *iid* is not bundled.
        """
        cls = type(self)
        if cls._instances_full_cache is None:
            cls._instances_full_cache = json.loads((self.fixtures_dir / "instances_full.json").read_text())
        by_id = {row["instance_id"]: row for row in cls._instances_full_cache}
        if iid not in by_id:
            raise KeyError(f"unknown instance_id {iid!r}; available: {sorted(by_id)}")
        return by_id[iid]

    # ------------------------------------------------------------------
    # resolve
    # ------------------------------------------------------------------

    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:
        """Build a ``DatasetHandle`` for the requested instance subset.

        The *digest* is ``sha256(sorted(instance_ids) + [suite_version])``,
        so it is deterministic and changes whenever the subset or pinned
        version changes.

        If ``cfg`` contains ``"instance_ids"``, those are used directly.
        Otherwise the bundled ``instances_subset.json`` fixture is read.
        """
        if "instance_ids" in cfg:
            instance_ids: list[str] = list(cfg["instance_ids"])
        else:
            raw: list[dict] = json.loads((self.fixtures_dir / "instances_subset.json").read_text())
            instance_ids = [inst["instance_id"] for inst in raw]

        # Deterministic digest: sorted ids + pinned version
        canonical = json.dumps(sorted(instance_ids) + [self.suite_version], sort_keys=True)
        digest = _sha256_bytes(canonical.encode())

        return DatasetHandle(
            version=self.suite_version,
            digest=digest,
            payload={"instance_ids": instance_ids, "agent_kind": str(cfg.get("agent_kind", "gold"))},
        )

    # ------------------------------------------------------------------
    # prepare
    # ------------------------------------------------------------------

    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:
        """Materialise the execution environment for a SWE-bench run.

        Creates a temporary working directory and assembles the full ``EnvHandle``
        payload that ``run()`` reads exclusively.  The payload contract is:

        .. code-block:: python

            {
                "instance_ids":     list[str],   # forwarded from dataset
                "agent_kind":       str,          # "oracle" | "llm" (real SUT) or
                                                  # "gold" | "empty" (MockAgent)
                "_tmp_dir":         str,          # path to a newly created tmpdir
                "dataset_name":     str,          # "princeton-nlp/SWE-bench_Verified"
                "split":            str,          # "test"
                "run_id":           str,          # "csbench-<8-hex>" deterministic hash
                "harness_timeout_s": float,       # 3600.0 seconds per instance
                "_sut":             SweSutClient, # ONLY when target.mock is False AND
                                                  # target.handle carries an adapter
            }

        ``_sut`` is the real-SUT seam: when ``target.mock`` is ``False`` and the
        orchestrator wired an adapter into ``Target.handle``, the SUT runtime is
        provisioned here and the client stashed under ``"_sut"`` for ``run()``;
        ``teardown()`` closes it.  The gated docker smoke drives prepare() with
        ``mock=False, handle=None`` — no SUT is provisioned and ``run()`` falls
        back to the MockAgent (gold/empty).
        """
        tmp_dir = tempfile.mkdtemp(prefix="csbench-swebench-")
        instance_ids = list(dataset.payload["instance_ids"])
        run_id = "csbench-" + hashlib.sha256(json.dumps(sorted(instance_ids)).encode()).hexdigest()[:8]
        payload: dict[str, Any] = {
            "instance_ids": instance_ids,
            "agent_kind": dataset.payload.get("agent_kind", "gold"),
            "_tmp_dir": tmp_dir,
            "dataset_name": self.dataset_name,
            "split": self.split,
            "run_id": run_id,
            "harness_timeout_s": 3600.0,
        }
        if not target.mock and target.handle is not None:
            from clousight_bench.suites.swe_bench.sut_client import SweSutClient

            sut = SweSutClient(target.handle)
            # agent_kind is known here — thread it so llm mode can forward the
            # driver-held DASHSCOPE_API_KEY into the provision-time runtime env.
            sut.provision(agent_mode=str(payload["agent_kind"]))
            payload["_sut"] = sut
        return EnvHandle(payload)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:
        """Drive the agent (real SUT or MockAgent) then the upstream harness.

        Agent selection splits on the ``"_sut"`` payload key (set by prepare()):

        - ``_sut`` present (real target): each instance's full dataset row is
          solved on the AgentRun-hosted agent via :class:`SweSutClient` — REAL
          predictions, REAL trajectory spans, REAL usage events.
        - ``_sut`` absent (the gated docker smoke, which hand-drives run() with
          ``agent_kind`` gold/empty): the MockAgent produces the patch and the
          canned trajectory/usage fixtures are copied, exactly as before.

        If the ``[swebench]`` extra is importable *and* ``driver.placement=="local"``,
        the upstream harness (``python -m swebench.harness.run_evaluation``) is
        invoked via subprocess.  Otherwise a ``RuntimeError`` is raised with a
        clear message.
        """
        instance_ids: list[str] = list(env.payload.get("instance_ids", []))
        if not instance_ids:
            raise RuntimeError(
                "env.payload has no instance_ids — prepare() must run before run(); "
                "a real SWE-bench run must never silently evaluate 0 instances"
            )

        if importlib.util.find_spec("swebench") is None:
            raise RuntimeError(
                "swebench extra not installed; "
                "install with 'pip install clousight-bench[swebench]' "
                "or use mock_artifacts() for the docker-free path"
            )
        if driver.placement != "local":
            raise RuntimeError(
                f"driver.placement must be 'local' for the real SWE-bench harness "
                f"(Docker runs on the local machine); got {driver.placement!r}"
            )

        tmp_dir = Path(env.payload["_tmp_dir"])
        tmp_dir.mkdir(parents=True, exist_ok=True)

        agent_kind: str = env.payload["agent_kind"]
        sut = env.payload.get("_sut")  # SweSutClient when prepare() provisioned a real SUT
        mock_agent: Any = None
        if sut is not None:
            # A default agent_kind="gold" must never silently become a paid real run.
            if agent_kind not in ("oracle", "llm"):
                raise RuntimeError(
                    f"real SUT run requires agent_kind 'oracle' or 'llm', got "
                    f"{agent_kind!r} — gold/empty are mock-agent modes"
                )
        else:
            from clousight_bench.suites.swe_bench.mock_agent import MockAgent

            mock_agent = MockAgent()  # hoisted: fixture JSON is read once, not per instance
        model_name = _model_name(agent_kind)

        # Write predictions.jsonl — every line must carry model_name_or_path so the harness
        # can embed it in the report filename.  Real path: the deployed agent solves each
        # full dataset row; spans/usage are collected for the REAL trajectory + usage files.
        span_lines: list[dict[str, Any]] = []
        usage_lines: list[dict[str, Any]] = []
        predictions_path = tmp_dir / "predictions.jsonl"
        with predictions_path.open("w") as fh:
            for iid in instance_ids:
                if sut is not None:
                    result = sut.solve(self._load_instance(iid), agent_kind)
                    patch = result["model_patch"]
                    span_lines.extend(result["spans"])
                    usage_lines.extend(result["usage_events"])
                else:
                    patch = mock_agent.patch_for(iid, agent_kind)
                fh.write(
                    json.dumps(
                        {
                            "instance_id": iid,
                            "model_name_or_path": model_name,
                            "model_patch": patch,
                        }
                    )
                    + "\n"
                )

        # Invoke upstream harness — run_id is guaranteed by prepare() (Task 2).
        run_id: str = env.payload["run_id"]
        cmd = [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--predictions_path",
            str(predictions_path),
            "--run_id",
            run_id,
            "--dataset_name",
            env.payload["dataset_name"],
            "--split",
            env.payload["split"],
            "--instance_ids",
            *instance_ids,
            "--max_workers",
            "1",
            "--report_dir",
            str(tmp_dir),
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                cwd=str(tmp_dir),
                capture_output=True,
                text=True,
                timeout=float(env.payload["harness_timeout_s"]),
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"swebench harness failed (exit {exc.returncode}): ...{(exc.stderr or '')[-2000:]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"swebench harness timed out after {env.payload['harness_timeout_s']}s"
            ) from exc

        # Trajectory + usage: REAL (from the SUT's spans/usage events) on the
        # real path; canned fixtures only on the MockAgent smoke path.
        traj_path = tmp_dir / "trajectory.jsonl"
        usage_path = tmp_dir / "usage.jsonl"
        if sut is not None:
            traj_path.write_text("".join(json.dumps(s) + "\n" for s in span_lines))
            usage_path.write_text("".join(json.dumps(u) + "\n" for u in usage_lines))
        else:
            shutil.copy(self.fixtures_dir / "trajectory.jsonl", traj_path)
            shutil.copy(self.fixtures_dir / "usage.jsonl", usage_path)

        # Locate the upstream harness report at the exact path it is documented to produce:
        #   <report_dir>/<model_name_or_path.replace('/', '__')>.<run_id>.json
        results_path = tmp_dir / "results.json"
        report_path = tmp_dir / f"{model_name.replace('/', '__')}.{run_id}.json"
        if not report_path.exists():
            nearby = sorted(p.name for p in tmp_dir.glob("*.json") if p.name != "results.json")
            raise RuntimeError(
                f"expected harness report {report_path.name!r} not found in {tmp_dir}; "
                f"json files present: {nearby}"
            )

        # Normalise into the suite's canonical shape via the pure normalizer.
        raw_report: dict[str, Any] = json.loads(report_path.read_text())
        normalised: dict[str, Any] = _normalize_upstream_report(raw_report, instance_ids)
        results_path.write_text(json.dumps(normalised))

        manifest: dict[str, dict[str, Any]] = {
            "predictions": {
                "path": "predictions.jsonl",
                "sha256": _sha256_file(predictions_path),
                "rows": _count_jsonl_rows(predictions_path),
            },
            "results": {
                "path": "results.json",
                "sha256": _sha256_file(results_path),
                "rows": None,
            },
            "trajectory": {
                "path": "trajectory.jsonl",
                "sha256": _sha256_file(traj_path),
                "rows": _count_jsonl_rows(traj_path),
            },
            "usage": {
                "path": "usage.jsonl",
                "sha256": _sha256_file(tmp_dir / "usage.jsonl"),
                "rows": _count_jsonl_rows(tmp_dir / "usage.jsonl"),
            },
        }
        return RawArtifacts(dir=tmp_dir, manifest=manifest)

    # ------------------------------------------------------------------
    # teardown
    # ------------------------------------------------------------------

    def teardown(self, env: EnvHandle) -> None:
        """Best-effort SUT deprovision, then removal of THIS run's harness containers.

        The SUT client (``"_sut"`` payload key, set by prepare() on the real
        target path) is closed FIRST so a cloud runtime never outlives the run.
        Docker cleanup filters docker ps by the run_id name filter so concurrent
        runs on the same host are never affected (never images, never prune).
        All failures are swallowed — teardown is best-effort by the
        BenchmarkSuite contract.
        """
        sut = env.payload.get("_sut")
        if sut is not None:
            try:
                sut.close()
            except Exception:  # noqa: BLE001 - teardown is best-effort by contract
                pass
        run_id = str(env.payload.get("run_id", ""))
        if not run_id or shutil.which("docker") is None:
            return
        try:
            ps = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"name={run_id}"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            ids = [c for c in ps.stdout.split() if c]
            if ids:
                subprocess.run(
                    ["docker", "rm", "-f", *ids],
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
        except Exception:  # noqa: BLE001 - teardown is best-effort by contract
            return

    # ------------------------------------------------------------------
    # provenance scaffold (agent identity — mode-aware)
    # ------------------------------------------------------------------

    def scaffold(self, params: dict[str, Any], *, mock: bool) -> str:
        """The agent scaffold that produced the artifacts (Provenance.scaffold).

        A mock run is ALWAYS the slice-1 mock-agent pin regardless of agent_kind
        (canned fixtures must never claim a real-SUT scaffold). A non-mock run
        derives the slice-2 scaffold from ``params['agent_kind']`` (oracle/llm);
        anything else keeps the slice-1 mock-agent pin. Shared by the Lite /
        Multimodal subclasses. Values are pinned — changing them moves the
        benchmark fingerprint.
        """
        if mock:
            return "mock-agent@slice1"
        return {"oracle": "oracle@slice2", "llm": "qwen-llm@slice2"}.get(
            str(params.get("agent_kind") or ""), "mock-agent@slice1"
        )

    # ------------------------------------------------------------------
    # mock_artifacts
    # ------------------------------------------------------------------

    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:
        """Copy the bundled fixture files into a temp dir and return ``RawArtifacts``.

        This path requires no Docker, no network, and no ``[swebench]`` extra.
        It is the recommended path for CI and offline testing.

        The manifest contains 4 keys: ``predictions``, ``results``,
        ``trajectory``, ``usage``.  All sha256 hashes reflect the actual file
        content at call time.
        """
        tmp_dir = Path(cfg["_tmp_dir"]) if "_tmp_dir" in cfg else Path(tempfile.mkdtemp())
        tmp_dir.mkdir(parents=True, exist_ok=True)

        fixture_map = {
            "predictions": ("predictions.jsonl", _count_jsonl_rows),
            "results": ("results.json", _count_json_rows),
            "trajectory": ("trajectory.jsonl", _count_jsonl_rows),
            "usage": ("usage.jsonl", _count_jsonl_rows),
        }

        manifest: dict[str, dict[str, Any]] = {}
        for key, (filename, row_counter) in fixture_map.items():
            src = self.fixtures_dir / filename
            dst = tmp_dir / filename
            shutil.copy(src, dst)
            manifest[key] = {
                "path": filename,
                "sha256": _sha256_file(dst),
                "rows": row_counter(dst),
            }

        return RawArtifacts(dir=tmp_dir, manifest=manifest)
