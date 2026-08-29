"""Data-warehouse domain pack.

Benchmarks OLAP SQL analytics engines (the class of system TPC-DS targets):
star-schema query execution, planner quality, and throughput -- NOT storage
durability or transactional semantics.

Suite-first: no self-designed task dimensions ship here. Recognized benchmarks
(TPC-DS and friends) populate this domain via the benchmark_suite / evaluator
contract in later tasks. This module ships only the domain declaration and the
``duckdb-local`` single-node reference platform (the analog of agent-runtime's
``local-sim``): a provider-less, simulated engine that proves the pipeline
end-to-end without any cloud account.
"""

from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
from clousight_bench.domains.data_warehouse.adapters.duckdb_local import DuckDbLocalAdapter


class DataWarehouseDomain(DomainPack):
    domain = "data-warehouse"
    description = "OLAP SQL analytics engines: star-schema query execution, planning, throughput."

    def tasks(self) -> dict[str, type[Task]]:
        # Suite-first: recognized benchmarks (TPC-DS ...) drive this domain via
        # the benchmark_suite / evaluator contract. No dimensions ship here.
        return {}

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {DuckDbLocalAdapter.name: DuckDbLocalAdapter}
