"""The benchmark_suite / evaluator plugin contract.

A ``BenchmarkSuite`` drives a recognized suite's OWN upstream harness unmodified
and returns opaque ``RawArtifacts``. An ``Evaluator`` reads those artifacts (a
pure function — no cloud, no credentials) into ``Measurement``s. Core treats the
handles as opaque, reading only ``DatasetHandle.{version, digest}`` for the
benchmark fingerprint; only the paired evaluator understands the artifacts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clousight_bench.core.observation import ItemResult, Measurement

TARGET_MODES: tuple[str, ...] = ("endpoint", "runtime")
PLACEMENTS: tuple[str, ...] = ("local", "in_cloud")


@dataclass
class DatasetHandle:
    version: str
    digest: str
    payload: dict[str, Any] = field(default_factory=dict)  # suite-private


@dataclass
class EnvHandle:
    payload: dict[str, Any] = field(default_factory=dict)  # suite-private


@dataclass
class RawArtifacts:
    dir: Path
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)

    def path(self, name: str) -> Path:
        return self.dir / self.manifest[name]["path"]


@dataclass
class Target:
    mode: str
    mock: bool
    handle: Any = None
    region: str = ""
    endpoint: str = ""
    credentials_ref: str = ""

    def __post_init__(self) -> None:
        if self.mode not in TARGET_MODES:
            raise ValueError(f"Target.mode must be one of {TARGET_MODES}, got {self.mode!r}")


@dataclass
class DriverContext:
    placement: str

    def __post_init__(self) -> None:
        if self.placement not in PLACEMENTS:
            raise ValueError(f"DriverContext.placement must be one of {PLACEMENTS}, got {self.placement!r}")


class BenchmarkSuite(ABC):
    """A benchmark — the primary public extension unit.

    Wrap a recognized upstream harness (SWE-bench, TPC-DS, MMLU, …) or any
    reproducible workload. Register a subclass under the
    ``clousight_bench.benchmark_suites`` entry-point group and pair it with an
    :class:`Evaluator`; run it with ``csbench run --benchmark <suite_id>``.

    Lifecycle the orchestrator drives: ``resolve`` (pick the dataset, offline) →
    ``prepare`` (connect/provision) → ``run`` (produce raw artifacts) →
    ``teardown``; ``mock_artifacts`` is the offline path used by tests / CI /
    ``mode: mock``. Set ``suite_id`` (matches the entry-point + the ``suite:``
    id + the measurement namespace) and ``suite_version`` (a provenance pin that
    moves the benchmark fingerprint when the dataset/harness changes).
    """

    suite_id: str = "abstract"
    suite_version: str = "0"
    requires_plugin_api: str = ">=3.0,<4.0"

    @abstractmethod
    def resolve(self, cfg: dict[str, Any], assets: Any) -> DatasetHandle:
        """Pick the dataset/subset for this run and return a :class:`DatasetHandle`
        (``version`` + a deterministic ``digest`` + a suite-private ``payload``).
        Must be cheap and offline (a pin read / bundled-fixture lookup) — no
        network, no credentials; it also runs to compute the benchmark fingerprint."""

    @abstractmethod
    def prepare(self, target: Target, dataset: DatasetHandle, driver: DriverContext) -> EnvHandle:
        """Set up the run environment for ``target`` (config-connect to an
        already-running SUT, or provision one) and return an :class:`EnvHandle`
        payload that ``run`` reads. May touch the cloud / read credentials."""

    @abstractmethod
    def run(self, target: Target, env: EnvHandle, driver: DriverContext) -> RawArtifacts:
        """Drive the workload and return :class:`RawArtifacts` (a directory + a
        manifest of named files) — raw, replayable evidence only, no scoring."""

    def teardown(self, env: EnvHandle) -> None:
        """Release anything ``prepare``/``run`` created. Always called (even on
        failure). Default no-op — connect-only suites need nothing."""
        return None

    @abstractmethod
    def mock_artifacts(self, cfg: dict[str, Any]) -> RawArtifacts:
        """Return :class:`RawArtifacts` from a bundled offline fixture — no
        network, no credentials, no SUT. The path CI / ``mode: mock`` uses; must
        produce the same artifact shape ``run`` does so the evaluator is identical."""

    def scaffold(self, params: dict[str, Any], *, mock: bool) -> str:  # noqa: ARG002
        """Provenance scaffold tag for this run — the SUT harness/agent identity
        that produced the artifacts, recorded in ``Provenance.scaffold``.

        Default is empty (most benchmarks connect to an endpoint / run a fixed
        harness — there is no separate agent scaffold to attribute). Agent suites
        (SWE-bench) override this to pin the agent scaffold, mode-aware, so a mock
        fixture run never claims a real-SUT scaffold. Lives on the suite, not in
        core, so suite-specific scaffold knowledge stays out of the orchestrator.
        """
        return ""


class Evaluator(ABC):
    """Scores a suite's :class:`RawArtifacts` into namespaced :class:`Measurement`s.

    A pure function of the artifacts — no cloud, no credentials — so a stored run
    is re-scorable. Register under ``clousight_bench.evaluators``; set
    ``evaluator_id`` and ``official`` (True = the suite's canonical numbers,
    emitted under the ``<suite_id>.`` namespace, which conformance enforces).
    """

    evaluator_id: str = "abstract"
    official: bool = True
    requires_plugin_api: str = ">=3.0,<4.0"
    # Composable add-on metrics applied over this evaluator's items() at
    # score time, emitted as ``<suite_id>.<metric_id>``. Empty = objective
    # evaluate() only. Bound metric ids must be registered under the
    # ``clousight_bench.metrics`` entry-point group (fail-loud if missing).
    extra_metric_ids: tuple[str, ...] = ()

    @abstractmethod
    def supports(self, suite_id: str, product: str) -> bool:
        """Whether this evaluator scores ``suite_id`` (usually
        ``suite_id == self.suite_id``). The registry picks a supporting
        evaluator, preferring ``official`` ones."""

    @abstractmethod
    def evaluate(self, raw: RawArtifacts) -> dict[str, Measurement]:
        """Map the artifacts to ``{"<suite_id>.<metric>": Measurement}``. Must be
        fail-safe — a missing/corrupt artifact returns ``{}``, never raises."""

    def items(self, raw: RawArtifacts) -> list[ItemResult]:  # noqa: ARG002
        """Optional per-item substrate (schema 0.4). Return per-example
        :class:`ItemResult`s whose scores the ``measurements`` aggregate. Default
        empty — a suite that has not migrated to per-item scoring still works; its
        record simply carries no ``items``."""
        return []


def evaluate_with_metrics(
    evaluator: Evaluator,
    raw: RawArtifacts,
    *,
    suite_id: str,
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Measurement], list[ItemResult]]:
    """Run an evaluator's objective ``evaluate()`` PLUS any add-on metrics,
    returning ``(measurements, items)``.

    Add-on metrics come from ``evaluator.extra_metric_ids`` (bound by the suite)
    AND ``params['extra_metrics']`` (opt-in per run), applied over the evaluator's
    ``items()`` and merged as ``<suite_id>.<metric_id>``; per-item scores are
    appended to the items.

    A judge-based metric (e.g. ``response-quality``) is fed a live
    :class:`JudgeModel` built from ``params['judge']`` (config-connect via the
    ``clousight_bench.judges`` seam), so it runs end-to-end against a real judge
    endpoint. With no judge configured the judge is ``None`` and such metrics skip
    cleanly (mock / conformance / CI never call a judge). NOTE: a run that
    configures a judge does network I/O here and its judge-based measurements are
    not offline-re-scorable — by design (``reproducibility_class="judge-based"``).
    Shared by SuiteRunner.score and the suite conformance command so the namespace
    guarantee covers metric outputs too. No items → no add-on metrics.
    """
    params = params or {}
    measurements = dict(evaluator.evaluate(raw))
    items = evaluator.items(raw)
    metric_ids = tuple(dict.fromkeys((*evaluator.extra_metric_ids, *params.get("extra_metrics", []))))
    if metric_ids and items:
        from clousight_bench.core.metric import MetricContext
        from clousight_bench.core.metric_runner import run_metrics
        from clousight_bench.core.registry import build_judge, load_metrics

        metrics = list(load_metrics(only=metric_ids).values())
        ctx = MetricContext(params=dict(params), judge=build_judge(params.get("judge")))
        items, extra = run_metrics(items, metrics, namespace=suite_id, ctx=ctx)
        measurements.update(extra)
    return measurements, items
