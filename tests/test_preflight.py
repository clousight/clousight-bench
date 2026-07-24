"""Preflight gate: prerequisites are checked before provisioning, not mid-run."""
from clousight_bench.core import preflight as pf
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def _clear_aws(monkeypatch, tmp_path):
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                "ALIBABA_CLOUD_PROFILE", "ALIBABA_CLOUD_ACCESS_KEY_ID",
                "ALIBABA_CLOUD_ACCESS_KEY_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.aws or ~/.alibabacloud here


# --- unit: report + check functions -----------------------------------------

def test_report_ok_ignores_warnings():
    r = pf.PreflightReport().add(
        pf.Check("a", ok=True),
        pf.Check("b", ok=False, severity=pf.WARNING),
    )
    assert r.ok is True
    assert r.critical_failures == []


def test_report_blocks_on_critical():
    r = pf.PreflightReport().add(pf.Check("creds", ok=False, severity=pf.CRITICAL,
                                          remediation="export keys"))
    assert r.ok is False
    assert "creds" in r.summary()


def test_credential_check_provider_less_is_passing_warning():
    c = pf.credential_check({}, "local-sim")
    assert c.ok and c.severity == pf.WARNING


def test_mock_localhost_is_critical_fail():
    c = pf.mock_reachable_check("http://127.0.0.1:8770")
    assert not c.ok and c.severity == pf.CRITICAL


def test_mock_unset_is_critical_fail():
    c = pf.mock_reachable_check("")
    assert not c.ok and c.severity == pf.CRITICAL


# --- adapter.preflight ------------------------------------------------------

def test_local_sim_preflight_passes():
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    assert LocalSimAdapter().preflight().ok is True


def test_real_adapter_preflight_fails_without_prereqs(monkeypatch, tmp_path):
    _clear_aws(monkeypatch, tmp_path)
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter

    report = AliyunAgentRunAdapter({"region": "cn-hangzhou"}).preflight()
    assert report.ok is False
    names = {c.name for c in report.critical_failures}
    assert "credentials" in names and "mock_base_url" in names


# --- orchestrator gate: abort BEFORE setup/execute --------------------------

def test_run_aborts_at_preflight_not_midrun(monkeypatch, tmp_path):
    _clear_aws(monkeypatch, tmp_path)
    spec = RunSpec("agent-runtime", "T1.3", "aliyun-agentrun",
                   target={"region": "cn-hangzhou"})
    rec = execute(spec, results_dir=tmp_path)
    assert rec.ok is False
    assert rec.metrics["preflight_ok"] is False
    assert rec.error.startswith("preflight failed")
    # proves we stopped at the gate, not inside run_tool_plan (which raises NotWired)
    assert "NotImplemented" not in (rec.error or "")
    assert "credentials" in rec.error


def test_skip_preflight_reaches_the_real_failure(monkeypatch, tmp_path):
    _clear_aws(monkeypatch, tmp_path)
    spec = RunSpec("agent-runtime", "T1.3", "aliyun-agentrun",
                   target={"region": "cn-hangzhou"})
    rec = execute(spec, results_dir=tmp_path, preflight=False)
    # gate off -> we fail LATER, mid-run (mock unreachable / skeleton NotWired),
    # exactly the late error the preflight gate exists to prevent.
    assert rec.ok is False
    assert "preflight_ok" not in rec.metrics
    assert rec.error


def test_local_sim_run_still_works_with_preflight(tmp_path):
    rec = execute(RunSpec("agent-runtime", "T1.3", "local-sim"), results_dir=tmp_path)
    assert rec.ok is True
    assert "preflight_ok" not in rec.metrics  # normal run, not a preflight abort
