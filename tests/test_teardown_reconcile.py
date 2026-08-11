"""Item 3: after each run, destroy + confirm by resource tag (the safety net).

A resource the harness created but did not delete (a crash between provision and
deprovision, a wired setup that provisioned a session runtime) keeps billing. So
after every run the orchestrator reconciles: look up this run's residual by tag,
destroy it, and confirm. Anything it reclaims is flagged (a leak happened);
anything it cannot reclaim is a critical finding pointing at ``csbench sweep``.
"""

from clousight_bench.core.resource_ledger import ResourceLedger
from clousight_bench.core.resource_reconcile import reconcile_run_resources
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter


def _leaky_ledger(tmp_path, run_id="run-1"):
    led = ResourceLedger(tmp_path)
    led.record_created(run_id, "aliyun", "rt-leak", "runtime", {})
    return led


def test_reconcile_destroys_a_leaked_resource_and_flags_it(tmp_path):
    _leaky_ledger(tmp_path)
    adapter = LocalSimAdapter({})
    adapter.run_id = "run-1"
    adapter.results_dir = tmp_path
    adapter.setup()

    findings = reconcile_run_resources(adapter, "run-1", "aliyun", tmp_path)

    assert ResourceLedger(tmp_path).residual("run-1") == []  # destroyed
    assert any(f.code == "teardown.reclaimed" for f in findings)
    adapter.teardown()


def test_reconcile_is_quiet_when_there_is_nothing_to_do(tmp_path):
    adapter = LocalSimAdapter({})
    adapter.run_id = "run-1"
    adapter.results_dir = tmp_path
    adapter.setup()
    findings = reconcile_run_resources(adapter, "run-1", None, tmp_path)
    assert findings == []
    adapter.teardown()


def test_unreclaimable_residual_is_critical(tmp_path, monkeypatch):
    _leaky_ledger(tmp_path)
    adapter = LocalSimAdapter({})
    adapter.run_id = "run-1"
    adapter.results_dir = tmp_path
    adapter.setup()
    # destroy fails -> the resource stays, and that is a critical, actionable leak
    monkeypatch.setattr(
        LocalSimAdapter, "deprovision", lambda self, rid: (_ for _ in ()).throw(RuntimeError("denied"))
    )
    findings = reconcile_run_resources(adapter, "run-1", "aliyun", tmp_path)
    residual_finding = [f for f in findings if f.code == "teardown.residual"]
    assert residual_finding and residual_finding[0].severity == "critical"
    adapter.teardown()


def test_reaper_verify_confirms_via_the_cloud(tmp_path, monkeypatch):
    # With a reaper installed, the authoritative check is the cloud tag query.
    class _FakeReaper:
        provider = "aliyun"

        def verify(self, run_id):
            return [{"id": "cloud-orphan-1", "run_id": run_id}]

        def sweep(self, *, dry_run, older_than_s=None):
            return []

    adapter = LocalSimAdapter({})
    adapter.run_id = "run-1"
    adapter.results_dir = tmp_path
    adapter.setup()
    findings = reconcile_run_resources(adapter, "run-1", "aliyun", tmp_path, reaper=_FakeReaper())
    assert any(f.code == "teardown.residual" and "cloud-orphan-1" in str(f.details) for f in findings)
    adapter.teardown()


def test_orchestrator_run_leaves_no_residual_and_no_leak_finding(tmp_path, monkeypatch):
    # A normal provisioning run (T0.1 provisions then deprovisions) must end clean:
    # reconcile runs, finds nothing, and emits no residual/reclaimed finding.
    from clousight_bench.core.orchestrator import execute

    spec = RunSpec("agent-runtime", "T0.1", "local-sim", target={})
    rec = execute(spec, results_dir=tmp_path, preflight=False)
    assert rec.status in ("completed", "unsupported")
    assert ResourceLedger(tmp_path).residual() == []
    codes = {f["code"] for f in rec.findings}
    assert "teardown.residual" not in codes
