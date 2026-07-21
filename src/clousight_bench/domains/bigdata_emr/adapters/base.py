"""Big-data cluster adapter interface.

Unlike agent-runtime (an always-on API), a big-data cluster must be provisioned
before load and destroyed after -- this is exactly what the orchestrator's
setup()/teardown() hooks are for. Tasks in this domain drive a WorkloadEngine
(YCSB / TPC-DS / terasort / wordcount) through the cluster's submit interface and
never care which cloud is underneath.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter
from clousight_bench.core.workload import WorkloadResult


class BigDataClusterAdapter(ProviderAdapter):
    """Uniform interface for a submittable big-data cluster.

    ``target`` keys used by real adapters (see configs/bigdata-emr.*.example.yaml):
    region, cluster_size, instance_type, release_label, auth env-var names,
    terraform_dir (for Terraform-provisioned clusters).
    """

    name = "abstract-bigdata"

    @abstractmethod
    def submit(self, workload_dir: str, params: dict[str, Any]) -> WorkloadResult:
        """Run one workload (via WorkloadEngine) on this cluster and return its
        parsed metrics. Provisioning/teardown happen in setup()/teardown()."""
