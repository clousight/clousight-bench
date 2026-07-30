"""Domain pack discovery.

Domains (built-in and third-party, including closed-source commercial packs)
all register through the ``clousight_bench.domains`` entry point group -- the
core never imports a domain by path. Installing a plugin package is enough for
``csbench list`` to see it.
"""
from __future__ import annotations

from importlib.metadata import entry_points

from clousight_bench.core.errors import UnknownDomainError, UserInputError
from clousight_bench.core.plugin import (
    DomainPack,
    PrivateAssetResolver,
    ResultEnricher,
    RuntimeProviderPlugin,
)

ENTRY_POINT_GROUP = "clousight_bench.domains"
ENRICHER_ENTRY_POINT_GROUP = "clousight_bench.enrichers"
ASSET_RESOLVER_ENTRY_POINT_GROUP = "clousight_bench.asset_resolvers"
RUNTIME_PROVIDER_ENTRY_POINT_GROUP = "clousight_bench.runtime_providers"


class RegistryError(UserInputError):
    pass


def load_domains() -> dict[str, DomainPack]:
    """Instantiate every installed domain pack, keyed by its domain name."""
    domains: dict[str, DomainPack] = {}
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        pack_cls = ep.load()
        pack = pack_cls()
        if not isinstance(pack, DomainPack):
            raise RegistryError(f"entry point {ep.name!r} is not a DomainPack")
        domains[pack.domain] = pack
    return domains


def get_domain(name: str) -> DomainPack:
    domains = load_domains()
    if name not in domains:
        available = ", ".join(sorted(domains)) or "<none installed>"
        raise UnknownDomainError(
            f"domain {name!r} not found. Installed domains: {available}"
        )
    return domains[name]


def load_enrichers() -> list[ResultEnricher]:
    """Instantiate every installed enricher, ordered by name for determinism."""
    enrichers: list[ResultEnricher] = []
    for ep in entry_points(group=ENRICHER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, ResultEnricher):
            raise RegistryError(f"entry point {ep.name!r} is not a ResultEnricher")
        enrichers.append(inst)
    return sorted(enrichers, key=lambda e: e.name)


def load_runtime_providers() -> dict[str, RuntimeProviderPlugin]:
    """Instantiate every installed runtime-provider plugin, keyed by ``provider``.

    Open-core installs none, so this is empty until a commercial pack is
    installed -> skeleton clouds keep falling back to the not-wired transport
    and stay un-runnable in real mode. Installing a pack registers a provider
    here, which flips that cloud's real mode to runnable."""
    providers: dict[str, RuntimeProviderPlugin] = {}
    for ep in entry_points(group=RUNTIME_PROVIDER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, RuntimeProviderPlugin):
            raise RegistryError(f"entry point {ep.name!r} is not a RuntimeProviderPlugin")
        providers[inst.provider] = inst
    return providers


def get_runtime_provider(provider: str | None) -> RuntimeProviderPlugin | None:
    """The wired runtime provider for ``provider``, or None if not installed."""
    if not provider:
        return None
    return load_runtime_providers().get(provider)


def load_asset_resolvers() -> list[PrivateAssetResolver]:
    """Instantiate every installed private asset resolver, ordered by name.

    Open-core ships none, so this is empty until a commercial pack is installed
    -> private assets raise NeedLicense with a clear message."""
    resolvers: list[PrivateAssetResolver] = []
    for ep in entry_points(group=ASSET_RESOLVER_ENTRY_POINT_GROUP):
        cls = ep.load()
        inst = cls()
        if not isinstance(inst, PrivateAssetResolver):
            raise RegistryError(f"entry point {ep.name!r} is not a PrivateAssetResolver")
        resolvers.append(inst)
    return sorted(resolvers, key=lambda r: r.name)
