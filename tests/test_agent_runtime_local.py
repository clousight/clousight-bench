"""End-to-end local baseline: T1.3 produces new three-state shape via local-sim.

No cloud account, no fixed port -- proves the framework itself before any real
adapter exists.
"""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_local_sim_t1_3_produces_new_shape(tmp_path):
    """Local-sim T1.3 returns recovered, observed_attempts, recovery_ms, platform_terminated."""
    spec = RunSpec(
        domain="agent-runtime",
        task_id="T1.3",
        platform="local-sim",
        target={"recovery": {"mode": "auto-retry"}},
    )
    rec = execute(spec, results_dir=tmp_path)
    assert rec.status == "completed"
    # New shape
    assert rec.measurements["recovered"]["value"] is True
    assert rec.measurements["observed_attempts"]["value"] >= 1
    assert rec.measurements["recovery_ms"]["unit"] == "ms"
    assert rec.measurements["platform_terminated"]["value"] is False
    # Old keys must be absent
    assert "recovery_mode" not in rec.measurements
    assert "final_state" not in rec.measurements
    assert rec.fingerprints.benchmark.startswith("sha256:")


def test_local_sim_t1_3_no_findings_on_healthy_platform(tmp_path):
    """Local-sim healthy path: recovered=True → no findings."""
    rec = execute(
        RunSpec("agent-runtime", "T1.3", "local-sim"),
        results_dir=tmp_path,
    )
    assert rec.status == "completed"
    assert rec.measurements["recovered"]["value"] is True
    assert rec.findings == []


def test_recovery_policy_changes_the_environment_fingerprint(tmp_path):
    retry = execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    fail = execute(
        RunSpec("agent-runtime", "T1.3", "local-sim", target={"recovery": {"mode": "fail-fast"}}),
        results_dir=tmp_path,
    )
    assert retry.fingerprints.environment != fail.fingerprints.environment
    assert retry.fingerprints.benchmark == fail.fingerprints.benchmark


def test_result_file_persisted(tmp_path):
    execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    files = list((tmp_path / "agent-runtime" / "local-sim").glob("T1.3-*.json"))
    assert files
