"""Local-process cluster adapter.

Proves the big-data domain end-to-end WITHOUT any cloud account: it runs the
workload as a local subprocess (via WorkloadEngine) instead of submitting to a
remote cluster. This is the EMR-domain analogue of agent-runtime's local-sim --
it validates that the SAME lifecycle + cross-language workload protocol carries a
non-agent product category, which is the whole point of the unified abstraction.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from clousight_bench.core.workload import WorkloadEngine, WorkloadResult
from clousight_bench.domains.bigdata_emr.adapters.base import BigDataClusterAdapter


class LocalProcessAdapter(BigDataClusterAdapter):
    name = "local-process"

    def submit(self, workload_dir: str, params: dict[str, Any]) -> WorkloadResult:
        engine = WorkloadEngine(Path(workload_dir))
        timeout = int(self.target.get("timeout_s", 600))
        return engine.run(params, timeout_s=timeout)
