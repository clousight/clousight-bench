"""End-to-end local baseline: the harness distinguishes auto-retry from fail-fast.

No cloud account, no fixed port -- proves the framework itself before any real
adapter exists.
"""
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_local_sim_auto_retry_recovers(tmp_path):
    spec = RunSpec(domain="agent-runtime", task_id="T1.3", platform="local-sim",
                   target={"recovery": {"mode": "auto-retry"}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.ok
    assert rec.evidence_layer == "C"
    assert rec.metrics["recovery_mode"] == "auto-retry"
    assert rec.metrics["final_state"] == "completed"
    assert rec.metrics["budgeted_success"] is True
    assert rec.config_hash.startswith("sha256:")


def test_local_sim_fail_fast_aborts(tmp_path):
    spec = RunSpec(domain="agent-runtime", task_id="T1.3", platform="local-sim",
                   target={"recovery": {"mode": "fail-fast"}})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.ok  # the test itself succeeded: it observed a fault and classified it
    assert rec.metrics["recovery_mode"] == "fail-fast"
    assert rec.metrics["final_state"] == "aborted"
    assert rec.metrics["budgeted_success"] is False


def test_recovery_policy_changes_config_hash(tmp_path):
    retry = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                            target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    fail = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                           target={"recovery": {"mode": "fail-fast"}}), results_dir=tmp_path)
    assert retry.config_hash != fail.config_hash


def test_result_file_persisted(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    files = list((tmp_path / "agent-runtime" / "local-sim").glob("T1.3-*.json"))
    assert files
