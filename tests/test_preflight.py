"""Preflight gate: prerequisites are checked before provisioning, not mid-run."""
from clousight_bench.core import preflight as pf
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
    AliyunAgentRunAdapter,
)


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
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
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
    monkeypatch.setattr(AliyunAgentRunAdapter, "status", "wired")
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


# --- per-benchmark x cloud minimal permission mapping ------------------------

def test_required_actions_differ_per_task():
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
    from clousight_bench.domains.agent_runtime.tasks.t2_1_tool_registration import (
        ToolRegistrationTask,
    )
    from clousight_bench.domains.agent_runtime.tasks.t4_2_otel_export import OtelExportTask

    a = AliyunAgentRunAdapter({"region": "cn-hangzhou"})
    reg_actions, _ = a.required_actions(ToolRegistrationTask())
    otel_actions, _ = a.required_actions(OtelExportTask())

    assert reg_actions == ["agentrun:RegisterTool"]
    # T4.2 needs session + invoke + export -> a different, larger set
    assert "agentrun:ExportTrace" in otel_actions
    assert "agentrun:RegisterTool" not in otel_actions
    assert reg_actions != otel_actions


def test_required_actions_differ_per_cloud():
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import (
        AliyunAgentRunAdapter,
        HuaweiAgentArtsAdapter,
    )
    from clousight_bench.domains.agent_runtime.tasks.t4_1_trace_completeness import (
        TraceCompletenessTask,
    )

    task = TraceCompletenessTask()
    aliyun, _ = AliyunAgentRunAdapter({}).required_actions(task)
    huawei, _ = HuaweiAgentArtsAdapter({}).required_actions(task)
    assert "agentrun:GetTrace" in aliyun
    assert "agentarts:trace:get" in huawei
    assert aliyun != huawei  # same benchmark, different cloud -> different actions


def test_unmapped_token_is_a_warning_not_a_block(monkeypatch, tmp_path):
    _clear_aws(monkeypatch, tmp_path)
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter

    class _Fake:
        task_id = "TX"
        required_permissions = ("bogus:token",)

    checks = AliyunAgentRunAdapter({"region": "cn-hangzhou"}).check_permissions(_Fake())
    mapping = [c for c in checks if c.name == "permissions:mapping"][0]
    assert mapping.severity == pf.WARNING and not mapping.ok


def test_permission_check_surfaces_minimal_actions(monkeypatch, tmp_path):
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
    from clousight_bench.domains.agent_runtime.tasks.t1_2_state_persistence import (
        StatePersistenceTask,
    )

    checks = AliyunAgentRunAdapter({}).check_permissions(StatePersistenceTask())
    perm_check = [c for c in checks if c.name.startswith("permissions[")][0]
    # skeleton can't verify -> warning that lists the minimal actions it WOULD need
    assert perm_check.severity == pf.WARNING
    assert "agentrun:PutSessionState" in perm_check.detail


def test_wired_probe_makes_permissions_critical(monkeypatch, tmp_path):
    """When an adapter can verify, missing permissions become a CRITICAL block."""
    _clear_aws(monkeypatch, tmp_path)
    from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
    from clousight_bench.domains.agent_runtime.tasks.t1_3_fault_recovery import FaultRecoveryTask

    adapter = AliyunAgentRunAdapter({"region": "cn-hangzhou"})
    # simulate a wired probe that finds one action missing
    monkeypatch.setattr(adapter, "_probe_permissions",
                        lambda actions: (False, ["agentrun:InvokeAgent"]))
    checks = adapter.check_permissions(FaultRecoveryTask())
    perm_check = [c for c in checks if c.name.startswith("permissions[")][0]
    assert perm_check.severity == pf.CRITICAL and not perm_check.ok
    assert "agentrun:InvokeAgent" in perm_check.remediation
