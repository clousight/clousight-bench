"""Domain pack discovery.

Domains (built-in and third-party, including closed-source commercial packs)
all register through the ``clousight_bench.domains`` entry point group -- the
core never imports a domain by path. Installing a plugin package is enough for
``csbench list`` to see it.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from clousight_bench.core.errors import UnknownDomainError, UserInputError
from clousight_bench.core.plugin import (
    DomainPack,
    PrivateAssetResolver,
    ResourceReaper,
    ResultEnricher,
    RuntimeProviderPlugin,
)
from clousight_bench.core.versioning import range_contains

if TYPE_CHECKING:
    from clousight_bench.core.judge import JudgeModel, JudgeProvider
    from clousight_bench.core.metric import Metric
    from clousight_bench.core.suite import BenchmarkSuite, Evaluator
    from clousight_bench.core.tracing import SpanExporter

ENTRY_POINT_GROUP = "clousight_bench.domains"
ENRICHER_ENTRY_POINT_GROUP = "clousight_bench.enrichers"
ASSET_RESOLVER_ENTRY_POINT_GROUP = "clousight_bench.asset_resolvers"
RUNTIME_PROVIDER_ENTRY_POINT_GROUP = "clousight_bench.runtime_providers"
RESOURCE_REAPER_ENTRY_POINT_GROUP = "clousight_bench.resource_reapers"
SPAN_EXPORTER_ENTRY_POINT_GROUP = "clousight_bench.span_exporters"


class RegistryError(UserInputError):
    pass


class IncompatiblePluginError(RegistryError):
    """A plugin declares a plugin-API range that excludes this core."""


class DuplicatePluginError(RegistryError):
    """Two installed plugins claim the same name."""


def _check_api_version(ep: object, obj: object) -> None:
    """Reject a plugin whose declared plugin-API range excludes this core."""
    from clousight_bench import PLUGIN_API_VERSION

    rng = getattr(obj, "requires_plugin_api", ">=1.0,<2.0")
    if not range_contains(rng, PLUGIN_API_VERSION):
        dist = getattr(getattr(ep, "dist", None), "name", None)
        where = f" from {dist}" if dist else ""
        name = getattr(ep, "name", "?")
        raise IncompatiblePluginError(
            f"plugin {name!r}{where} requires plugin-API {rng!r} but this "
            f"clousight-bench provides {PLUGIN_API_VERSION!r}; upgrade the core "
            f"or install a compatible plugin version"
        )


def check_domain_conflicts(pack: DomainPack) -> None:
    """Reject a domain whose task_ids or adapter names collide."""
    task_ids: dict[str, str] = {}
    for key, task_cls in pack.tasks().items():
        tid = getattr(task_cls, "task_id", key)
        if tid in task_ids:
            raise DuplicatePluginError(
                f"domain {pack.domain!r}: task_id {tid!r} is claimed by both "
                f"{task_ids[tid]} and {task_cls.__name__}"
            )
        task_ids[tid] = task_cls.__name__
    names: dict[str, str] = {}
    for key, ad_cls in pack.adapters().items():
        nm = getattr(ad_cls, "name", key)
        if nm in names:
            raise DuplicatePluginError(
                f"domain {pack.domain!r}: platform name {nm!r} is claimed by both "
                f"{names[nm]} and {ad_cls.__name__}"
            )
        names[nm] = ad_cls.__name__


def load_domains() -> dict[str, DomainPack]:
    """Instantiate every installed domain pack, keyed by its domain name."""
    domains: dict[str, DomainPack] = {}
    seen: dict[str, str] = {}  # domain name -> entry-point name
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        pack_cls = ep.load()
        pack = pack_cls()
        if not isinstance(pack, DomainPack):
            raise RegistryError(f"entry point {ep.name!r} is not a DomainPack")
        _check_api_version(ep, pack)
        if pack.domain in seen:
            raise DuplicatePluginError(
                f"domain {pack.domain!r} is provided by two plugins: {seen[pack.domain]!r} and {ep.name!r}"
            )
        check_domain_conflicts(pack)
        seen[pack.domain] = ep.name
        domains[pack.domain] = pack
    return domains


def get_domain(name: str) -> DomainPack:
    domains = load_domains()
    if name not in domains:
        available = ", ".join(sorted(domains)) or "<none installed>"
        raise UnknownDomainError(f"domain {name!r} not found. Installed domains: {available}")
    return domains[name]


def load_enrichers() -> list[ResultEnricher]:
    """Instantiate every installed enricher, ordered by name for determinism."""
    enrichers: list[ResultEnricher] = []
    seen: dict[str, str] = {}
    for ep in entry_points(group=ENRICHER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, ResultEnricher):
            raise RegistryError(f"entry point {ep.name!r} is not a ResultEnricher")
        _check_api_version(ep, inst)
        if inst.name in seen:
            raise DuplicatePluginError(
                f"enricher name {inst.name!r} is provided by two plugins: {seen[inst.name]!r} and {ep.name!r}"
            )
        seen[inst.name] = ep.name
        enrichers.append(inst)
    return sorted(enrichers, key=lambda e: e.name)


def load_runtime_providers() -> dict[str, RuntimeProviderPlugin]:
    """Instantiate every installed runtime-provider plugin, keyed by ``provider``.

    Open-core installs none, so this is empty until a commercial pack is
    installed -> skeleton clouds keep falling back to the not-wired transport
    and stay un-runnable in real mode. Installing a pack registers a provider
    here, which flips that cloud's real mode to runnable."""
    providers: dict[str, RuntimeProviderPlugin] = {}
    seen: dict[str, str] = {}
    for ep in entry_points(group=RUNTIME_PROVIDER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, RuntimeProviderPlugin):
            raise RegistryError(f"entry point {ep.name!r} is not a RuntimeProviderPlugin")
        _check_api_version(ep, inst)
        if inst.provider in seen:
            raise DuplicatePluginError(
                f"runtime provider {inst.provider!r} is provided by two plugins: "
                f"{seen[inst.provider]!r} and {ep.name!r}"
            )
        seen[inst.provider] = ep.name
        providers[inst.provider] = inst
    return providers


def get_runtime_provider(provider: str | None) -> RuntimeProviderPlugin | None:
    """The wired runtime provider for ``provider``, or None if not installed."""
    if not provider:
        return None
    return load_runtime_providers().get(provider)


def load_resource_reapers() -> dict[str, ResourceReaper]:
    """Instantiate every installed resource-reaper plugin, keyed by ``provider``.

    Open-core installs none (listing/deleting cloud resources needs the provider
    SDK + credentials), so ``csbench sweep`` fails clearly until a pack registers
    one via the ``clousight_bench.resource_reapers`` entry point."""
    reapers: dict[str, ResourceReaper] = {}
    seen: dict[str, str] = {}
    for ep in entry_points(group=RESOURCE_REAPER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, ResourceReaper):
            raise RegistryError(f"entry point {ep.name!r} is not a ResourceReaper")
        _check_api_version(ep, inst)
        if inst.provider in seen:
            raise DuplicatePluginError(
                f"resource reaper {inst.provider!r} is provided by two plugins: "
                f"{seen[inst.provider]!r} and {ep.name!r}"
            )
        seen[inst.provider] = ep.name
        reapers[inst.provider] = inst
    return reapers


def get_resource_reaper(provider: str | None) -> ResourceReaper | None:
    """The installed resource reaper for ``provider``, or None if not installed."""
    if not provider:
        return None
    return load_resource_reapers().get(provider)


def load_span_exporters() -> list[SpanExporter]:
    """Instantiate every installed execution-trace span exporter, ordered by name.

    Open-core ships the local file exporter (spans land as JSONL under
    ``<results>/traces/``); a commercial pack can register a remote OTLP exporter
    through the same entry point without any core change."""
    from clousight_bench.core.tracing import SpanExporter

    exporters: list[SpanExporter] = []
    seen: dict[str, str] = {}
    for ep in entry_points(group=SPAN_EXPORTER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, SpanExporter):
            raise RegistryError(f"entry point {ep.name!r} is not a SpanExporter")
        _check_api_version(ep, inst)
        if inst.name in seen:
            raise DuplicatePluginError(
                f"span exporter name {inst.name!r} is provided by two plugins: "
                f"{seen[inst.name]!r} and {ep.name!r}"
            )
        seen[inst.name] = ep.name
        exporters.append(inst)
    return sorted(exporters, key=lambda e: e.name)


def load_asset_resolvers() -> list[PrivateAssetResolver]:
    """Instantiate every installed private asset resolver, ordered by name.

    Open-core ships none, so this is empty until a commercial pack is installed
    -> private assets raise NeedLicense with a clear message."""
    resolvers: list[PrivateAssetResolver] = []
    seen: dict[str, str] = {}
    for ep in entry_points(group=ASSET_RESOLVER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, PrivateAssetResolver):
            raise RegistryError(f"entry point {ep.name!r} is not a PrivateAssetResolver")
        _check_api_version(ep, inst)
        if inst.name in seen:
            raise DuplicatePluginError(
                f"asset resolver name {inst.name!r} is provided by two plugins: "
                f"{seen[inst.name]!r} and {ep.name!r}"
            )
        seen[inst.name] = ep.name
        resolvers.append(inst)
    return sorted(resolvers, key=lambda r: r.name)


BENCHMARK_SUITE_ENTRY_POINT_GROUP = "clousight_bench.benchmark_suites"
EVALUATOR_ENTRY_POINT_GROUP = "clousight_bench.evaluators"
METRIC_ENTRY_POINT_GROUP = "clousight_bench.metrics"
JUDGE_ENTRY_POINT_GROUP = "clousight_bench.judges"


def load_benchmark_suites() -> dict[str, BenchmarkSuite]:
    from clousight_bench.core.suite import BenchmarkSuite

    suites: dict[str, BenchmarkSuite] = {}
    for ep in entry_points(group=BENCHMARK_SUITE_ENTRY_POINT_GROUP):
        inst = ep.load()()
        if not isinstance(inst, BenchmarkSuite):
            raise RegistryError(f"entry point {ep.name!r} is not a BenchmarkSuite")
        _check_api_version(ep, inst)
        if inst.suite_id in suites:
            raise DuplicatePluginError(f"suite {inst.suite_id!r} provided twice")
        suites[inst.suite_id] = inst
    return suites


def load_evaluators() -> list[Evaluator]:
    from clousight_bench.core.suite import Evaluator

    out: list[Evaluator] = []
    for ep in sorted(entry_points(group=EVALUATOR_ENTRY_POINT_GROUP), key=lambda e: e.name):
        inst = ep.load()()
        if not isinstance(inst, Evaluator):
            raise RegistryError(f"entry point {ep.name!r} is not an Evaluator")
        _check_api_version(ep, inst)
        out.append(inst)
    return out


def load_metrics(only: tuple[str, ...] | None = None) -> dict[str, Metric]:
    """Discover composable metrics (R2). ``only`` filters to specific metric ids;
    a requested id that is not registered raises so a suite's binding fails loud."""
    from clousight_bench.core.metric import Metric

    metrics: dict[str, Metric] = {}
    for ep in entry_points(group=METRIC_ENTRY_POINT_GROUP):
        inst = ep.load()()
        if not isinstance(inst, Metric):
            raise RegistryError(f"entry point {ep.name!r} is not a Metric")
        _check_api_version(ep, inst)
        if inst.metric_id in metrics:
            raise DuplicatePluginError(f"metric {inst.metric_id!r} provided twice")
        metrics[inst.metric_id] = inst
    if only is not None:
        missing = [m for m in only if m not in metrics]
        if missing:
            raise RegistryError(f"unknown metric id(s): {missing}; registered: {sorted(metrics)}")
        return {m: metrics[m] for m in only}
    return metrics


def load_judge_providers() -> dict[str, JudgeProvider]:
    """Discover judge providers (R3b) — the config-connect seam for LLM-as-judge
    (open-source + commercial). Keyed by provider name."""
    from clousight_bench.core.judge import JudgeProvider

    providers: dict[str, JudgeProvider] = {}
    for ep in entry_points(group=JUDGE_ENTRY_POINT_GROUP):
        inst = ep.load()()
        if not isinstance(inst, JudgeProvider):
            raise RegistryError(f"entry point {ep.name!r} is not a JudgeProvider")
        _check_api_version(ep, inst)
        if inst.name in providers:
            raise DuplicatePluginError(f"judge provider {inst.name!r} provided twice")
        providers[inst.name] = inst
    return providers


def build_judge(config: dict[str, Any] | None) -> JudgeModel | None:
    """Build a JudgeModel from run config via the selected provider, or None.

    ``config`` shape: ``{"provider": "<name>", ...provider-specific...}``. Returns
    None when no judge is configured (``config`` empty / no ``provider``) so a
    judge-based metric skips cleanly. An unknown provider name fails loud. A
    ``cache`` path in the config wraps the judge in a content-addressed
    :class:`CachingJudge` (R6) so repeat verdicts skip the LLM call.
    """
    if not config:
        return None
    name = config.get("provider")
    if not name:
        return None
    providers = load_judge_providers()
    if name not in providers:
        raise RegistryError(f"unknown judge provider {name!r}; registered: {sorted(providers)}")
    judge = providers[name].build(dict(config))
    cache_path = config.get("cache")
    if cache_path:
        from clousight_bench.core.judge import CachingJudge

        judge = CachingJudge(judge, cache_path)
    return judge
