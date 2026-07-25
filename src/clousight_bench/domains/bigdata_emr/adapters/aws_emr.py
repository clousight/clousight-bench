"""AWS EMR cluster adapter (skeleton).

Demonstrates the Terraform-backed provisioning path: setup() would `terraform
apply` the module under infra/terraform/aws-emr to spin up a cluster, submit()
would run a Spark/Hive step and collect step metrics, and teardown() would
`terraform destroy` so a forgotten cluster never keeps billing.

This is an honest skeleton: it fails with a clear message until wired to an
account, and it must NEVER reimplement task or scoring logic. Filling it in is a
provisioning + submit exercise, not a benchmark-design one.

target keys (see configs/bigdata-emr.aws.example.yaml):
    region, release_label, master_instance_type, core_instance_type,
    core_instance_count, log_uri, subnet_id, auth_env, terraform_dir
"""
from __future__ import annotations

from typing import Any

from clousight_bench.core.workload import WorkloadResult
from clousight_bench.domains.bigdata_emr.adapters.base import BigDataClusterAdapter


class _NotWiredError(NotImplementedError):
    def __init__(self) -> None:
        super().__init__(
            "aws-emr is a skeleton. To benchmark a real EMR cluster: set target.terraform_dir "
            "to infra/terraform/aws-emr, provide AWS credentials via the env vars named in "
            "target.auth_env, and implement setup()/submit()/teardown() (terraform apply -> "
            "add_job_flow_steps -> terraform destroy). Docs: "
            "https://docs.aws.amazon.com/emr/"
        )


class AwsEmrAdapter(BigDataClusterAdapter):
    name = "aws-emr"
    status = "skeleton"
    provider = "aws"

    def describe(self) -> dict[str, Any]:
        desc = super().describe()
        # cluster shape is part of what a result means -> fold into config_hash
        for key in ("region", "release_label", "core_instance_type", "core_instance_count"):
            if key in self.target:
                desc[key] = self.target[key]
        return desc

    def setup(self) -> None:
        raise _NotWiredError()

    def submit(self, workload_dir: str, params: dict[str, Any]) -> WorkloadResult:
        raise _NotWiredError()

    def teardown(self) -> None:
        # Safe to call even if setup() never provisioned anything.
        return None
