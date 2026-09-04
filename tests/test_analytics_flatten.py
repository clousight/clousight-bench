import json
from pathlib import Path

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.ops.analytics import Analytics


def _write(root: Path, run_id="r1"):
    payload = {
        "schema_version": "0.4",
        "run": {
            "run_id": run_id,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:01Z",
            "stages": {},
        },
        "identity": {
            "domain": "agent-runtime",
            "task_id": "suite:stub.ok",
            "adapter": "local-sim",
            "task_revision": "2",
            "scorer_revision": "2",
        },
        "environment": {"region": "cn-hangzhou", "mode": "mock"},
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b", "implementation": "sha256:c"},
        "measurements": {
            "cold_start_ms": {
                "value": 42.0,
                "unit": "ms",
                "reproducibility_class": "environmental",
                "aggregation": "p50",
                "sample_count": 5,
            },
            "recovery_mode": {"value": "auto-retry", "unit": "", "reproducibility_class": "deterministic"},
        },
        "findings": [
            {
                "code": "agent_runtime.scaling_knee",
                "severity": "warning",
                "summary": "knee at 8",
            }
        ],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {"pricing": {"cost_usd": 0.0123}},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    p = root / "agent-runtime" / "local-sim"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"suite:stub.ok-{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_flatten_records(tmp_path):
    _write(tmp_path)
    rows = Analytics(tmp_path).flatten("records")
    assert len(rows) == 1
    r = rows[0]
    assert r["platform"] == "local-sim" and r["mode"] == "mock"
    assert r["region"] == "cn-hangzhou" and r["status"] == "completed"
    assert r["cost_usd"] == 0.0123
    assert r["benchmark_fp"] == "sha256:a"


def test_flatten_measurements_num_vs_label(tmp_path):
    _write(tmp_path)
    rows = {m["name"]: m for m in Analytics(tmp_path).flatten("measurements")}
    assert rows["cold_start_ms"]["value_num"] == 42.0
    assert rows["cold_start_ms"]["value_str"] is None
    assert rows["cold_start_ms"]["aggregation"] == "p50"
    assert rows["cold_start_ms"]["sample_count"] == 5
    assert rows["recovery_mode"]["value_num"] is None
    assert rows["recovery_mode"]["value_str"] == "auto-retry"


def test_flatten_measurements_official(tmp_path):
    # official defaults to True when the measurement omits it; an explicit
    # non-official (custom-added) metric flattens to False.
    _write(tmp_path)
    rows = {m["name"]: m for m in Analytics(tmp_path).flatten("measurements")}
    assert rows["cold_start_ms"]["official"] is True  # omitted -> default True

    payload = json.loads((tmp_path / "agent-runtime" / "local-sim" / "suite:stub.ok-r1.json").read_text())
    payload["run"]["run_id"] = "r2"
    payload["measurements"] = {
        "difficulty_weighted": {
            "value": 0.7,
            "unit": "",
            "reproducibility_class": "deterministic",
            "official": False,
        }
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    out = tmp_path / "agent-runtime" / "local-sim" / "suite:stub.ok-r2.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    rows = {m["name"]: m for m in Analytics(tmp_path).flatten("measurements")}
    assert rows["difficulty_weighted"]["official"] is False


def test_flatten_findings(tmp_path):
    _write(tmp_path)
    rows = Analytics(tmp_path).flatten("findings")
    assert rows[0]["code"] == "agent_runtime.scaling_knee"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["platform"] == "local-sim"


def test_records_expose_list_and_discount(tmp_path):
    payload = {
        "schema_version": "0.4",
        "run": {"run_id": "rc", "stages": {}},
        "identity": {
            "domain": "agent-runtime",
            "task_id": "T5.1",
            "adapter": "aliyun-agentrun",
            "task_revision": "1",
            "scorer_revision": "2",
        },
        "environment": {"region": "cn-hangzhou", "mode": "cloud"},
        "fingerprints": {"benchmark": "sha256:a", "environment": "sha256:b", "implementation": "sha256:c"},
        "measurements": {},
        "findings": [],
        "observations": {},
        "series": {},
        "artifacts": [],
        "extensions": {"pricing": {"cost_usd": 0.7, "list_cost_usd": 1.0, "discount_usd": 0.3}},
        "errors": [],
        "status": "completed",
    }
    payload["fingerprints"]["record_digest"] = record_digest(payload)
    p = tmp_path / "agent-runtime" / "aliyun-agentrun"
    p.mkdir(parents=True)
    (p / "T5.1-rc.json").write_text(json.dumps(payload), encoding="utf-8")
    row = Analytics(tmp_path).flatten("records")[0]
    assert row["cost_usd"] == 0.7 and row["list_cost_usd"] == 1.0 and row["discount_usd"] == 0.3


def test_records_expose_execution(tmp_path):
    payload = {
        "schema_version": "0.4",
        "run": {"run_id": "re", "stages": {}},
        "identity": {
            "domain": "agent-runtime",
            "task_id": "suite:stub.alt",
            "adapter": "aliyun-agentrun",
            "task_revision": "1",
            "scorer_revision": "1",
        },
        "environment": {"region": "cn-hangzhou", "mode": "cloud", "execution": "simulated"},
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
    p = tmp_path / "agent-runtime" / "aliyun-agentrun"
    p.mkdir(parents=True)
    (p / "stub.alt-re.json").write_text(json.dumps(payload), encoding="utf-8")
    assert Analytics(tmp_path).flatten("records")[0]["execution"] == "simulated"
