"""Key-value / online-serving domain pack.

Benchmarks key-value / online-serving datastores (the class YCSB targets):
read/update/scan/insert throughput and tail latency under a controlled operation
mix — NOT analytical query performance.

Suite-first: no self-designed task dimensions ship here. Recognized benchmarks
(YCSB) drive this domain via the benchmark_suite / evaluator contract. Platforms
resolve a YCSB *binding* + endpoint from the run ``Target`` — the SUT-connection
abstraction: ``ycsb-local`` (binding=basic, in-memory reference) and
``ycsb-endpoint`` (config-connect to an already-running service). Cloud-managed
KV backends attach later on the same seam.
"""

from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.domains.key_value.adapters.ycsb import YcsbEndpointAdapter, YcsbLocalAdapter


class KeyValueDomain(DomainPack):
    domain = "key-value"
    description = "Key-value / online-serving datastores: read/update/scan throughput and tail latency."

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            YcsbLocalAdapter.name: YcsbLocalAdapter,
            YcsbEndpointAdapter.name: YcsbEndpointAdapter,
        }
