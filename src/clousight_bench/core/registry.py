"""Domain pack discovery.

Domains (built-in and third-party, including closed-source commercial packs)
all register through the ``clousight_bench.domains`` entry point group -- the
core never imports a domain by path. Installing a plugin package is enough for
``csbench list`` to see it.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

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
    from clousight_bench.core.reporting.renderers.base import ReportRenderer
    from clousight_bench.core.tracing import SpanExporter

ENTRY_POINT_GROUP = "clousight_bench.domains"
ENRICHER_ENTRY_POINT_GROUP = "clousight_bench.enrichers"
ASSET_RESOLVER_ENTRY_POINT_GROUP = "clousight_bench.asset_resolvers"
RUNTIME_PROVIDER_ENTRY_POINT_GROUP = "clousight_bench.runtime_providers"
RESOURCE_REAPER_ENTRY_POINT_GROUP = "clousight_bench.resource_reapers"
SPAN_EXPORTER_ENTRY_POINT_GROUP = "clousight_bench.span_exporters"
REPORT_RENDERER_ENTRY_POINT_GROUP = "clousight_bench.report_renderers"


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


def load_report_renderers() -> dict[str, ReportRenderer]:
    """Report renderers keyed by name. Core provides the built-in ``html``; a
    commercial/third-party pack adds more via the ``clousight_bench.report_renderers``
    entry point (e.g. a PDF or a themed HTML renderer)."""
    from clousight_bench.core.reporting.renderers.base import ReportRenderer
    from clousight_bench.core.reporting.renderers.html import HtmlRenderer

    renderers: dict[str, ReportRenderer] = {"html": HtmlRenderer()}
    for ep in entry_points(group=REPORT_RENDERER_ENTRY_POINT_GROUP):
        inst = ep.load()()
        if not isinstance(inst, ReportRenderer):
            raise RegistryError(f"entry point {ep.name!r} is not a ReportRenderer")
        renderers[inst.name] = inst
    return renderers
