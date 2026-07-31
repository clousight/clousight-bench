import json
from pathlib import Path

import pytest

from clousight_bench.core.fingerprints import record_digest


def _write_analytics_record(root: Path, run_id: str = "r1") -> None:
    """Write one digest-valid 0.2 record for the analytics tests."""
    payload = {
        "schema_version": "0.2",
        "run": {"run_id": run_id, "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:01Z", "stages": {}},
        "identity": {"domain": "agent-runtime", "task_id": "T1.3", "adapter": "local-sim",
                     "task_revision": "2", "scorer_revision": "2"},
        "environment": {"region": "cn-hangzhou", "mode": "mock"},
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b",
                         "implementation": "sha256:c"},
        "measurements": {
            "cold_start_ms": {"value": 42.0, "unit": "ms", "evidence": "B",
                              "aggregation": "p50", "sample_count": 5},
            "recovery_mode": {"value": "auto-retry", "unit": "", "evidence": "C"},
        },
        "findings": [{"code": "agent_runtime.scaling_knee", "severity": "warning",
                      "summary": "knee at 8", "evidence": "B"}],
        "observations": {}, "series": {}, "artifacts": [],
        "extensions": {"pricing": {"cost_usd": 0.0123}},
        "errors": [], "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    p = root / "agent-runtime" / "local-sim"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"T1.3-{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def write_record():
    """Return the analytics-record writer: write_record(root, run_id='r1')."""
    return _write_analytics_record
