"""Core engine: schema, plugin contracts, orchestrator, registry, report."""

from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.core.schema import ResultRecord, RunSpec

__all__ = ["DomainPack", "ProviderAdapter", "ResultRecord", "RunSpec"]
