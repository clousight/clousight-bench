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

from clousight_bench.core.observation import (
    Finding,
    Measurement,
    ObservationBundle,
    TaskResult,
)
from clousight_bench.core.plugin import ProviderAdapter, Task
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine
from clousight_bench.domains.bigdata_emr.adapters.base import BigDataClusterAdapter

# Which workload directory (cross-language, manifest-described) this task drives.
DEFAULT_WORKLOAD = "wordcount-py"


class WordcountSmokeTask(Task):
    task_id = "J1.1"
    title = "Batch job smoke (wordcount)"
    evidence_layer = "C"
    task_revision = "2"
    scorer_revision = "2"

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

    def workload_identity(self, params: dict[str, Any]) -> dict[str, Any]:
        described = WorkloadEngine(self._workload_dir(params)).describe()
        return {
            "workload": str(described["workload"]),
            "workload_version": str(described["workload_version"]),
            "assets": list(described["assets"]),
        }

    def environment_facts(self, adapter: ProviderAdapter, params: dict[str, Any]) -> dict[str, Any]:
        return {"workload": self._workload_dir(params).name}

    def execute(self, adapter: ProviderAdapter, params: dict[str, Any]) -> ObservationBundle:
        if not isinstance(adapter, BigDataClusterAdapter):
            raise TypeError("J1.1 needs a BigDataClusterAdapter")
        workload_dir = self._workload_dir(params)
        job_params = {
            "rows": params.get("rows", 100_000),
            "seed": params.get("seed", 42),
        }
        result = adapter.submit(str(workload_dir), job_params)
        return ObservationBundle(
            observations={
                "workload": workload_dir.name,
                "job_params": job_params,
                "raw_metrics": dict(result.metrics),
                "exit_code": result.exit_code,
                "ok": result.ok,
                "logs": list(result.logs[-20:]),
            },
            series=dict(result.series),
            artifacts=list(result.artifacts),
        )

    def score(self, observations: ObservationBundle) -> TaskResult:
        raw = observations.observations
        measurements = {
            name: Measurement(value=value, unit="", evidence="C")
            for name, value in sorted(raw.get("raw_metrics", {}).items())
        }
        succeeded = bool(raw.get("ok"))
        measurements["job_succeeded"] = Measurement(value=succeeded, unit="", evidence="C")
        findings: list[Finding] = []
        if not succeeded:
            findings.append(
                Finding(
                    code="bigdata.job_failed",
                    severity="critical",
                    summary="the batch job did not complete successfully",
                    evidence="C",
                    details={
                        "exit_code": raw.get("exit_code"),
                        "logs": raw.get("logs", []),
                    },
                )
            )
        return TaskResult(
            measurements=measurements,
            findings=findings,
            notes=(f"wordcount smoke via workload {raw.get('workload', '')}; ok={succeeded}"),
            task_revision=self.task_revision,
            scorer_revision=self.scorer_revision,
        )
