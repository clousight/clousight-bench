import json
from pathlib import Path

from clousight_bench.core.analytics import iter_verified_records
from clousight_bench.core.fingerprints import record_digest


def _write_record(root: Path, run_id: str, tamper: bool = False) -> None:
    payload = {
        "schema_version": "0.3",
        "run": {"run_id": run_id, "stages": {}},
        "identity": {
            "domain": "agent-runtime",
            "task_id": "stub.alt",
            "adapter": "local-sim",
            "task_revision": "1",
            "scorer_revision": "1",
        },
        "environment": {"region": "", "mode": "local"},
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b", "implementation": "sha256:c"},
        "measurements": {},
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    if tamper:
        payload["status"] = "failed"  # digest no longer matches
    p = root / "agent-runtime" / "local-sim"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"stub.alt-{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_yields_only_verified_records(tmp_path):
    _write_record(tmp_path, "good")
    _write_record(tmp_path, "bad", tamper=True)
    (tmp_path / "aggregates").mkdir()
    (tmp_path / "aggregates" / "agg.json").write_text('{"kind":"run_plan_aggregate"}', "utf-8")
    got = {payload["run"]["run_id"] for _, payload in iter_verified_records(tmp_path)}
    assert got == {"good"}
