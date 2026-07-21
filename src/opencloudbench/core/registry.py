"""Domain pack discovery.

Domains (built-in and third-party, including closed-source commercial packs)
all register through the ``opencloudbench.domains`` entry point group -- the
core never imports a domain by path. Installing a plugin package is enough for
``ocb list`` to see it.
"""
from __future__ import annotations

from importlib.metadata import entry_points

from opencloudbench.core.plugin import DomainPack

ENTRY_POINT_GROUP = "opencloudbench.domains"


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
