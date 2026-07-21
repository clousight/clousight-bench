"""Domain pack discovery.

Domains (built-in and third-party, including closed-source commercial packs)
all register through the ``clousight_bench.domains`` entry point group -- the
core never imports a domain by path. Installing a plugin package is enough for
``csbench list`` to see it.
"""
from __future__ import annotations

from importlib.metadata import entry_points

from clousight_bench.core.plugin import DomainPack, ResultEnricher

ENTRY_POINT_GROUP = "clousight_bench.domains"
ENRICHER_ENTRY_POINT_GROUP = "clousight_bench.enrichers"


class RegistryError(RuntimeError):
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
        raise RegistryError(f"domain {name!r} not found. Installed domains: {available}")
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
