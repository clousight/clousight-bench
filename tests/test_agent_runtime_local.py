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
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"] == {
        "value": "auto-retry", "unit": "", "evidence": "C"}
    assert rec.measurements["final_state"]["value"] == "completed"
    assert rec.measurements["budgeted_success"]["value"] is True
    assert rec.fingerprints.benchmark.startswith("sha256:")


def test_local_sim_fail_fast_aborts(tmp_path):
    spec = RunSpec(domain="agent-runtime", task_id="T1.3", platform="local-sim",
                   target={"recovery": {"mode": "fail-fast"}})
    rec = execute(spec, results_dir=tmp_path)
    # the benchmark itself succeeded: it observed a fault and classified it
    assert rec.status == "completed"
    assert rec.measurements["recovery_mode"]["value"] == "fail-fast"
    assert rec.measurements["final_state"]["value"] == "aborted"
    assert rec.measurements["budgeted_success"]["value"] is False
    assert [f["code"] for f in rec.findings] == ["agent_runtime.recovery_fail_fast"]


def test_recovery_policy_changes_the_environment_fingerprint(tmp_path):
    retry = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                            target={"recovery": {"mode": "auto-retry"}}), results_dir=tmp_path)
    fail = execute(RunSpec("agent-runtime", "T1.3", "local-sim",
                           target={"recovery": {"mode": "fail-fast"}}), results_dir=tmp_path)
    assert retry.fingerprints.environment != fail.fingerprints.environment
    assert retry.fingerprints.benchmark == fail.fingerprints.benchmark


def test_result_file_persisted(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    files = list((tmp_path / "agent-runtime" / "local-sim").glob("T1.3-*.json"))
    assert files
