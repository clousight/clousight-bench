"""J1.1 batch job smoke (wordcount).

The minimal task that proves the big-data domain: submit a deterministic batch
job to the cluster (via the cross-language WorkloadEngine) and record its
throughput / duration. It exists to validate that the unified lifecycle +
workload protocol carries a non-agent product -- NOT to be a serious big-data
benchmark (TPC-DS / terasort land later as their own workloads).

Evidence layer C: the input corpus is pinned by the workload, so the number is a
controlled-variable measurement of the cluster, reproducible on your own account.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.core.plugin import ProviderAdapter, Task, TaskOutput
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.domains.bigdata_emr.adapters.base import BigDataClusterAdapter

# Which workload directory (cross-language, manifest-described) this task drives.
DEFAULT_WORKLOAD = "wordcount-py"


class WordcountSmokeTask(Task):
    task_id = "J1.1"
    title = "Batch job smoke (wordcount)"
    evidence_layer = "C"

    def _workload_dir(self, params: dict[str, Any]) -> Path:
        workload = str(params.get("workload", DEFAULT_WORKLOAD))
        path = Path(workload)
        if path.is_absolute():
            return path
        return reference_workload_path(workload)

    def config(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workload": params.get("workload", DEFAULT_WORKLOAD),
            "rows": params.get("rows", 100_000),
            "seed": params.get("seed", 42),
        }

    def run(self, adapter: ProviderAdapter, params: dict[str, Any]) -> TaskOutput:
        assert isinstance(adapter, BigDataClusterAdapter), "J1.1 needs a BigDataClusterAdapter"
        workload_dir = self._workload_dir(params)
        job_params = {"rows": params.get("rows", 100_000), "seed": params.get("seed", 42)}

        result = adapter.submit(str(workload_dir), job_params)

        metrics = dict(result.metrics)
        metrics["job_succeeded"] = result.ok
        return TaskOutput(
            metrics=metrics,
            evidence_layer=self.evidence_layer,
            ok=result.ok,
            raw={"logs": result.logs[-20:], "exit_code": result.exit_code},
            notes=f"wordcount smoke via workload {workload_dir.name}; ok={result.ok}",
        )
