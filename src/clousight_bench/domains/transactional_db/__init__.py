"""Transactional (OLTP) database domain pack.

Benchmarks transactional databases (the class TPC-C targets): high-concurrency
OLTP throughput and tail latency under a controlled transaction mix — NOT
analytical query performance (that is the ``data-warehouse`` domain).

Suite-first: no self-designed task dimensions ship here. Recognized benchmarks
(TPC-C via BenchBase) drive this domain via the benchmark_suite / evaluator
contract. Platforms resolve BenchBase's ``dbtype`` + JDBC endpoint from the run
``Target`` — the SUT-connection abstraction: ``benchbase-local`` (dbtype=sqlite,
embedded reference) and ``jdbc-endpoint`` (config-connect to an already-running
database). Cloud-managed RDBMS backends attach later on the same seam.
"""

from __future__ import annotations

from clousight_bench.core.plugin import DomainPack, ProviderAdapter
from clousight_bench.domains.transactional_db.adapters.benchbase import (
    BenchbaseLocalAdapter,
    JdbcEndpointAdapter,
)


class TransactionalDbDomain(DomainPack):
    domain = "transactional-db"
    description = "Transactional (OLTP) databases: high-concurrency transaction throughput and tail latency."

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            BenchbaseLocalAdapter.name: BenchbaseLocalAdapter,
            JdbcEndpointAdapter.name: JdbcEndpointAdapter,
        }
