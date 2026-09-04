"""The stable public API for building on Clousight Bench.

Import the plugin contracts and data model from here (or from the top-level
``clousight_bench`` package), NOT from ``clousight_bench.core.*`` — the ``core``
layout is internal and may move; this facade is what the plugin-API version
(``clousight_bench.PLUGIN_API_VERSION``) governs.

Three layers, in the order most users need them:

1. **Eval** (add/score a benchmark): :class:`BenchmarkSuite` + :class:`Evaluator`
   (optionally :class:`Metric`; a :class:`JudgeProvider` is an implementation
   detail a Metric may use). Register under ``clousight_bench.benchmark_suites``
   / ``.evaluators`` (/ ``.metrics`` / ``.judges``) and run with
   ``csbench run --benchmark <id>``.
2. **Cloud lifecycle** (connect a SUT): :class:`DomainPack` +
   :class:`ProviderAdapter` (+ provider/reaper/enricher hooks).
3. **Campaign ops** (optional): ``clousight_bench.ops`` — run-plans and
   cross-record analytics over the single-run core.

    from clousight_bench.api import BenchmarkSuite, Evaluator, Measurement, ItemResult
"""

from __future__ import annotations

# --- plugin contracts -------------------------------------------------------
from clousight_bench.core.judge import (
    CachingJudge,
    JudgeError,
    JudgeModel,
    JudgeProvider,
    judge_emit,
)
from clousight_bench.core.metric import Metric, MetricContext
from clousight_bench.core.observation import (
    ITEM_SCORE_STATUSES,
    REPRODUCIBILITY_CLASSES,
    Finding,
    ItemResult,
    ItemScore,
    Measurement,
    ObservationBundle,
    TaskExecutionError,
    TaskResult,
)
from clousight_bench.core.plugin import (
    CampaignProbeHook,
    ControllerReaperSpec,
    ControllerTfSpec,
    DomainPack,
    PrivateAssetResolver,
    ProviderAdapter,
    ProvisionedCloudAdapter,
    ResourceReaper,
    ResultEnricher,
    RuntimeProviderPlugin,
)
from clousight_bench.core.record import Provenance
from clousight_bench.core.registry import (
    ASSET_RESOLVER_ENTRY_POINT_GROUP,
    BENCHMARK_SUITE_ENTRY_POINT_GROUP,
    ENRICHER_ENTRY_POINT_GROUP,
    EVALUATOR_ENTRY_POINT_GROUP,
    JUDGE_ENTRY_POINT_GROUP,
    METRIC_ENTRY_POINT_GROUP,
    RESOURCE_REAPER_ENTRY_POINT_GROUP,
    RUNTIME_PROVIDER_ENTRY_POINT_GROUP,
    SPAN_EXPORTER_ENTRY_POINT_GROUP,
    DuplicatePluginError,
    RegistryError,
    build_judge,
    load_benchmark_suites,
    load_evaluators,
    load_judge_providers,
    load_metrics,
)
from clousight_bench.core.registry import (
    ENTRY_POINT_GROUP as DOMAIN_ENTRY_POINT_GROUP,
)
from clousight_bench.core.schema import ResultRecord, RunSpec, new_run_id, utc_now
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    Evaluator,
    RawArtifacts,
    Target,
    evaluate_with_metrics,
)

__all__ = [
    # suites + evaluation
    "BenchmarkSuite",
    "Evaluator",
    "DatasetHandle",
    "EnvHandle",
    "RawArtifacts",
    "Target",
    "DriverContext",
    "evaluate_with_metrics",
    # metrics
    "Metric",
    "MetricContext",
    # judges
    "JudgeModel",
    "JudgeProvider",
    "JudgeError",
    "judge_emit",
    "CachingJudge",
    # data model (ObservationBundle/TaskResult are the record's evidence
    # containers — produced by the internal runner, not implemented by plugins)
    "Measurement",
    "Finding",
    "ItemResult",
    "ItemScore",
    "ObservationBundle",
    "TaskResult",
    "TaskExecutionError",
    "REPRODUCIBILITY_CLASSES",
    "ITEM_SCORE_STATUSES",
    "RunSpec",
    "ResultRecord",
    "Provenance",
    "new_run_id",
    "utc_now",
    # cloud-lifecycle contracts (layer 2)
    "DomainPack",
    "ProviderAdapter",
    "ProvisionedCloudAdapter",
    "ResultEnricher",
    "PrivateAssetResolver",
    "RuntimeProviderPlugin",
    "ResourceReaper",
    "CampaignProbeHook",
    "ControllerTfSpec",
    "ControllerReaperSpec",
    # registry + entry-point groups
    "load_benchmark_suites",
    "load_evaluators",
    "load_metrics",
    "load_judge_providers",
    "build_judge",
    "RegistryError",
    "DuplicatePluginError",
    "BENCHMARK_SUITE_ENTRY_POINT_GROUP",
    "EVALUATOR_ENTRY_POINT_GROUP",
    "METRIC_ENTRY_POINT_GROUP",
    "JUDGE_ENTRY_POINT_GROUP",
    "DOMAIN_ENTRY_POINT_GROUP",
    "ENRICHER_ENTRY_POINT_GROUP",
    "ASSET_RESOLVER_ENTRY_POINT_GROUP",
    "RUNTIME_PROVIDER_ENTRY_POINT_GROUP",
    "RESOURCE_REAPER_ENTRY_POINT_GROUP",
    "SPAN_EXPORTER_ENTRY_POINT_GROUP",
]
