"""Big-data (EMR-style) domain pack (skeleton).

Exists to prove the unified abstraction generalizes beyond agent runtimes: the
SAME lifecycle (provision -> submit -> collect -> teardown) and the SAME
cross-language workload protocol carry a completely different product category.
It intentionally ships ONE minimal task (J1.1 wordcount smoke) and TWO adapters
(local-process for no-account validation, aws-emr as a Terraform-backed skeleton).

Serious big-data dimensions (TPC-DS price/performance, terasort, shuffle
stress) land later as additional workloads under workloads/, reusing this pack.
"""
from __future__ import annotations

from opencloudbench.core.plugin import DomainPack, ProviderAdapter, Task
from opencloudbench.domains.bigdata_emr.adapters.aws_emr import AwsEmrAdapter
from opencloudbench.domains.bigdata_emr.adapters.local_process import LocalProcessAdapter
from opencloudbench.domains.bigdata_emr.tasks.j1_1_wordcount import WordcountSmokeTask


class BigDataEmrDomain(DomainPack):
    domain = "bigdata-emr"
    description = "Managed big-data clusters (EMR-style): batch job price/performance via borrowed workloads."

    def tasks(self) -> dict[str, type[Task]]:
        return {WordcountSmokeTask.task_id: WordcountSmokeTask}

    def adapters(self) -> dict[str, type[ProviderAdapter]]:
        return {
            LocalProcessAdapter.name: LocalProcessAdapter,
            AwsEmrAdapter.name: AwsEmrAdapter,
        }
