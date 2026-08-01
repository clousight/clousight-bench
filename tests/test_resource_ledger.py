"""Item 2: every provisioned cloud resource is tagged + booked in a run ledger.

Tagging alone is inert unless something applies it and something can reverse-look
it up. So the shared managed adapter stamps ``resource_tags()`` on every resource
it provisions (all four clouds inherit this) AND books it in a per-results-dir
ledger keyed by run id, so a resource the harness created but did not delete is
findable -- the substrate the post-run reconcile (item 3) reverse-looks-up.
"""
from clousight_bench.core.resource_ledger import ResourceLedger
from clousight_bench.core.resource_tags import TAG_RUN_ID
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

# --- the ledger ------------------------------------------------------------

def test_created_resource_is_residual_until_deleted(tmp_path):
    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "rt-1", "runtime", {TAG_RUN_ID: "run-1"})
    assert [e["resource_id"] for e in led.residual("run-1")] == ["rt-1"]
    led.mark_deleted("run-1", "rt-1")
    assert led.residual("run-1") == []


def test_residual_is_scoped_by_run(tmp_path):
    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "rt-1", "runtime", {})
    led.record_created("run-2", "aliyun", "rt-2", "runtime", {})
    assert [e["resource_id"] for e in led.residual("run-1")] == ["rt-1"]
    assert {e["resource_id"] for e in led.residual()} == {"rt-1", "rt-2"}


# --- tagging capability on the shared managed adapter ----------------------

def _adapter(tmp_path, **target):
    a = LocalSimAdapter(target)
    a.run_id = "run-xyz"
    a.results_dir = tmp_path
    a.setup()
    return a


def test_provision_stamps_run_id_tag(tmp_path):
    a = _adapter(tmp_path)
    result = a.provision({"artifact_ref": "pkg://x"})
    assert result.tags[TAG_RUN_ID] == "run-xyz"
    a.teardown()


def test_provision_books_the_resource_in_the_ledger(tmp_path):
    a = _adapter(tmp_path)
    result = a.provision({})
    residual = ResourceLedger(tmp_path).residual("run-xyz")
    assert [e["resource_id"] for e in residual] == [result.runtime_id]
    a.teardown()


def test_deprovision_clears_the_ledger_entry(tmp_path):
    a = _adapter(tmp_path)
    result = a.provision({})
    a.deprovision(result.runtime_id)
    assert ResourceLedger(tmp_path).residual("run-xyz") == []
    a.teardown()


def test_provision_without_deprovision_leaves_residual(tmp_path):
    # the crash-between-provision-and-deprovision case the safety net must catch
    a = _adapter(tmp_path)
    result = a.provision({})
    a.teardown()  # no deprovision
    residual = ResourceLedger(tmp_path).residual("run-xyz")
    assert [e["resource_id"] for e in residual] == [result.runtime_id]
