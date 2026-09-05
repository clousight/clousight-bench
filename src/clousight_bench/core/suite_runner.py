"""SuiteRunner: drives a BenchmarkSuite + Evaluator through the stage machine.

The internal runner behind every benchmark run — NOT a plugin contract. Plugin
authors implement :class:`BenchmarkSuite` / :class:`Evaluator` (and optionally
``Metric``); the orchestrator wraps the pair in a SuiteRunner, which owns
observation packing (``execute``), pure scoring (``score``), and the
``provenance()`` credibility chain threaded into the benchmark fingerprint and
the persisted ResultRecord.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clousight_bench.core.observation import ObservationBundle, TaskResult
from clousight_bench.core.record import Provenance
from clousight_bench.core.suite import (
    DriverContext,
    RawArtifacts,
    Target,
    evaluate_with_metrics,
)
from clousight_bench.core.sut_span import validate_span

if TYPE_CHECKING:
    from clousight_bench.core.plugin import ProviderAdapter
    from clousight_bench.core.suite import BenchmarkSuite, DatasetHandle, Evaluator


class SuiteRunner:
    """Internal runner that wraps a ``BenchmarkSuite`` and an ``Evaluator``.

    ``mock=True`` (the default): ``execute`` calls ``suite.mock_artifacts``
    instead of the real prepare/run chain.  This lets tests exercise
    the full stage machine without a live cloud target.

    The ``provenance()`` method returns the credibility chain for this run,
    which the orchestrator threads into the benchmark fingerprint and the
    persisted ResultRecord.

    ``params`` carries the RunSpec params forwarded by the bridge (including
    any ``evaluator`` key used for selection — it stays in params because a
    different evaluator is a different benchmark, and config() will include it
    in the fingerprint).

    ``artifacts_root`` is the directory under which all suite artifacts are
    staged after execute().  When supplied (typically ``results_dir/artifacts``
    from the orchestrator bridge), artifacts are copied there and the original
    suite scratch directory is removed.  When omitted, a fallback under
    ``tempfile.gettempdir()`` is used, but callers should always supply it so
    persisted records contain only relative paths (no absolute temp paths).
    """

    # Class defaults; __init__ overrides task_revision to the suite's
    # suite_version so the fingerprint tracks the dataset/harness. scorer_revision
    # stays "0" until an evaluator versions its scoring logic.
    task_revision: str = "0"
    scorer_revision: str = "0"
    title: str = ""
    # Abstract capability tokens / taxonomy tags; __init__ forwards the suite's
    # declarations (empty when a suite declares none). Preflight maps
    # required_permissions to each cloud's concrete minimal permissions.
    required_permissions: tuple[str, ...] = ()
    capability_tags: tuple[str, ...] = ()

    def __init__(
        self,
        suite: BenchmarkSuite,
        evaluator: Evaluator,
        *,
        mock: bool = True,
        params: dict[str, Any] | None = None,
        artifacts_root: Path | None = None,
        trace_id: str = "",
    ) -> None:
        self._suite = suite
        self._evaluator = evaluator
        self.mock = mock
        self.task_id: str = f"suite:{suite.suite_id}"
        # Instance attr shadows the class-level "0" so fingerprints track suite version.
        self.task_revision: str = suite.suite_version
        self._params: dict[str, Any] = dict(params or {})
        self._artifacts_root: Path | None = artifacts_root
        # Lazy cached DatasetHandle — populated on first call to _dataset().
        self._dataset_handle: DatasetHandle | None = None
        self.trace_id = trace_id
        self.required_permissions = tuple(getattr(suite, "required_permissions", ()) or ())
        self.capability_tags = tuple(getattr(suite, "capability_tags", ()) or ())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_artifacts_root(self) -> Path:
        """Return the directory under which staged artifacts are stored.

        If ``self._artifacts_root`` was set by the caller (i.e. the orchestrator
        bridge passed ``results_dir/artifacts``), that value is used.  Otherwise
        a per-process fallback is created under ``tempfile.gettempdir()``.
        Callers in production code must always supply ``artifacts_root`` so that
        persisted records contain only relative paths.
        """
        root = self._artifacts_root or Path(tempfile.gettempdir()) / "clousight-bench-artifacts"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _dataset(self) -> DatasetHandle:
        """Return the resolved DatasetHandle, computing it at most once.

        Uses constructor params only (not call-time params) so the digest is
        stable across the lifetime of this SuiteRunner.  The bridge passes
        spec.params into the constructor, so in the real path constructor and
        call-time params are the same dict; fingerprint stability requires the
        digest not to change if the caller later supplies extra call-time params.

        NOTE: resolve() is contractually cheap/offline (pin read or bundled
        fixture lookup) — it must NOT make network calls.  Suites that need a
        live manifest must cache it themselves.
        """
        if self._dataset_handle is None:
            self._dataset_handle = self._suite.resolve(dict(self._params), assets=None)
        return self._dataset_handle

    # ------------------------------------------------------------------
    # Runner lifecycle (the orchestrator's calling contract)
    # ------------------------------------------------------------------

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return the controlled inputs that determine the result.

        These feed the benchmark fingerprint alongside the provenance.
        """
        cfg: dict[str, Any] = {
            "suite_id": self._suite.suite_id,
            "suite_version": self._suite.suite_version,
            "evaluator_id": self._evaluator.evaluator_id,
        }
        cfg.update(params)
        return cfg

    def execute(
        self,
        adapter: ProviderAdapter | None,
        params: dict[str, Any],
    ) -> ObservationBundle:
        """Run the suite (or its mock), stage artifacts, and pack into a bundle.

        The suite's scratch directory (``raw.dir``) is removed after staging
        so it never leaks into the persisted record.  Observations use the
        relative ``artifacts_subdir`` key instead of the old absolute ``raw_dir``
        key, making records portable and operator-identity-safe.
        """
        if self.mock:
            raw = self._suite.mock_artifacts(params)
        else:
            # Thread the operator's connection config (endpoint / credentials ref)
            # from the adapter's target dict into the suite-facing Target, so a
            # suite can config-connect to an already-running service (e.g. YCSB's
            # ycsb-endpoint host:port). Absent keys stay at their empty defaults.
            adapter_target = dict(getattr(adapter, "target", {}) or {})
            target = Target(
                mode="endpoint",
                mock=False,
                handle=adapter,
                region=str(getattr(adapter, "region", "") or ""),
                endpoint=str(adapter_target.get("endpoint", "") or ""),
                credentials_ref=str(adapter_target.get("credentials_ref", "") or ""),
            )
            driver = DriverContext(placement="local", trace_id=self.trace_id)
            # Use the cached dataset handle from constructor params.  The bridge
            # passes spec.params into the constructor, so in the real path the
            # two dicts are identical.  Fingerprint stability requires the dataset
            # digest to be fixed at construction time — not at call time.
            dataset = self._dataset()
            env = self._suite.prepare(target, dataset, driver)
            try:
                raw = self._suite.run(target, env, driver)
            finally:
                # Best-effort by contract: reaps THIS run's harness containers and
                # scratch even when run() raises (timeout, harness failure, kill).
                self._suite.teardown(env)

        # Stage artifacts into a deterministically-named subdir under artifacts_root.
        root = self._resolve_artifacts_root()
        subdir = f"{self.task_id.replace(':', '-')}-{uuid.uuid4().hex[:8]}"
        stage_dir = root / subdir
        shutil.copytree(raw.dir, stage_dir)
        # Remove the suite's mkdtemp scratch — we now own a clean staged copy.
        shutil.rmtree(raw.dir, ignore_errors=True)

        traj = raw.manifest.get("trajectory")
        if traj is not None:
            traj_file = stage_dir / traj["path"]
            for lineno, line in enumerate(traj_file.read_text().splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    validate_span(json.loads(line))
                except (ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(f"invalid SUT span at {traj['path']}:{lineno}: {exc}") from exc

        artifacts: list[dict] = []
        if traj is not None:
            artifacts.append(
                {
                    "kind": "trajectory",
                    "media": "application/jsonl",
                    "sha256": traj["sha256"],
                    "path": f"{subdir}/{traj['path']}",
                }
            )

        return ObservationBundle(
            observations={
                "artifacts_subdir": subdir,
                "manifest": raw.manifest,
            },
            artifacts=artifacts,
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        """Reconstruct RawArtifacts from staged artifacts and evaluate.

        Reconstruction uses ``artifacts_root / artifacts_subdir`` so the
        evaluator reads from the same staged copy that execute() produced.
        No cloud calls, no I/O side-effects beyond reading local files.
        """
        subdir = observations.observations["artifacts_subdir"]
        stage_dir = self._resolve_artifacts_root() / subdir
        raw = RawArtifacts(
            dir=stage_dir,
            manifest=observations.observations["manifest"],
        )
        measurements, items = evaluate_with_metrics(
            self._evaluator, raw, suite_id=self._suite.suite_id, params=self._params
        )
        return TaskResult(measurements=measurements, items=items)

    def environment_facts(
        self,
        adapter: ProviderAdapter | None,  # noqa: ARG002
        params: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, Any]:
        """Non-sensitive environment facts folded into the environment fingerprint.

        Suites carry environment identity in their artifacts (e.g. engine
        versions), so the runner declares none. Never a credential, hostname,
        username or raw environment variable.
        """
        return {}

    # ------------------------------------------------------------------
    # Provenance
    # ------------------------------------------------------------------

    def workload_identity(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """Workload identity sourced from the suite and dataset for fingerprinting."""
        return {
            "workload": self._suite.suite_id,
            "workload_version": self._suite.suite_version,
            "assets": [self._dataset().digest],
        }

    def provenance(self) -> Provenance:
        """Build the credibility chain from the suite, dataset, and evaluator identity.

        Calls _dataset() to get the real digest from the suite's resolve() — cheap/offline
        by contract (see _dataset() docstring).  The digest is stable across the task
        lifetime because _dataset() is cached on constructor params.

        The scaffold tag comes from the suite (``suite.scaffold(params, mock=...)``)
        — suite-specific SUT/agent identity knowledge lives on the suite, not in
        this core shim. Agent suites (SWE-bench) return a mode-aware agent
        scaffold; non-agent suites return "" (no separate scaffold to attribute).
        """
        return Provenance(
            suite_id=self._suite.suite_id,
            suite_version=self._suite.suite_version,
            dataset_digest=self._dataset().digest,
            unmodified=True,
            evaluator_id=self._evaluator.evaluator_id,
            evaluator_official=self._evaluator.official,
            scaffold=self._suite.scaffold(self._params, mock=self.mock),
            division="",
        )
