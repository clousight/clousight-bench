"""SuiteTask: wraps a BenchmarkSuite + Evaluator into the Task plugin shape.

A SuiteTask is the thin glue between the benchmark-suite contract (Task 1) and
the orchestrator's stage machine.  It delegates the three Task lifecycle methods
to the suite/evaluator pair and exposes a ``provenance()`` method so the
orchestrator can thread the credibility chain into the benchmark fingerprint and
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
from clousight_bench.core.plugin import Task
from clousight_bench.core.record import Provenance
from clousight_bench.core.suite import (
    DriverContext,
    RawArtifacts,
    Target,
)
from clousight_bench.core.sut_span import validate_span

if TYPE_CHECKING:
    from clousight_bench.core.plugin import ProviderAdapter
    from clousight_bench.core.suite import BenchmarkSuite, DatasetHandle, Evaluator


class SuiteTask(Task):
    """Task adapter that wraps a ``BenchmarkSuite`` and an ``Evaluator``.

    ``mock=True`` (the default): ``execute`` calls ``suite.mock_artifacts``
    instead of the real prepare/run chain.  This lets the TDD slice exercise
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

    # task_revision / scorer_revision stay at "0" (default) for the slice-1
    # placeholder; suites that change their scoring logic bump these.
    task_revision: str = "0"
    scorer_revision: str = "0"

    def __init__(
        self,
        suite: BenchmarkSuite,
        evaluator: Evaluator,
        *,
        mock: bool = True,
        params: dict[str, Any] | None = None,
        artifacts_root: Path | None = None,
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
        stable across the lifetime of this SuiteTask.  The bridge passes
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
    # Task contract
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
            target = Target(
                mode="endpoint",
                mock=False,
                handle=adapter,
                region=str(getattr(adapter, "region", "") or ""),
            )
            driver = DriverContext(placement="local")
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
        measurements = self._evaluator.evaluate(raw)
        return TaskResult(measurements=measurements)

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

        The scaffold field is mode-aware: a mock run (self.mock) is ALWAYS the
        slice-1 mock-agent pin regardless of agent_kind — mock artifacts must
        never claim a real-SUT scaffold.  Only a non-mock run derives the slice-2
        scaffolds from params["agent_kind"] "oracle"/"llm"; anything else
        (including absent) keeps the slice-1 mock-agent pin.
        """
        if self.mock:
            scaffold = "mock-agent@slice1"
        else:
            scaffold = {"oracle": "oracle@slice2", "llm": "qwen-llm@slice2"}.get(
                str(self._params.get("agent_kind") or ""), "mock-agent@slice1"
            )
        return Provenance(
            suite_id=self._suite.suite_id,
            suite_version=self._suite.suite_version,
            dataset_digest=self._dataset().digest,
            unmodified=True,
            evaluator_id=self._evaluator.evaluator_id,
            evaluator_official=self._evaluator.official,
            scaffold=scaffold,
            division="",
        )
