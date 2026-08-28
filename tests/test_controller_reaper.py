"""Tests for RestrictedReaper — ledger-reverse-lookup delete, self last."""

from clousight_bench.core.resource_ledger import ResourceLedger, live_runtimes_from_ledger
from clousight_bench.domains.agent_runtime.controller_reaper import RestrictedReaper


def test_reap_order_runtime_then_nat_then_self_last():
    calls = []
    r = RestrictedReaper(
        live_runtimes=lambda: ["r1"],
        delete_runtime=lambda rid: calls.append(("runtime", rid)),
        delete_nat=lambda: calls.append(("nat", None)),
        delete_self=lambda sid: calls.append(("self", sid)),
        self_instance_id="i-self",
    )
    errors = r.reap()
    assert errors == []
    assert calls == [("runtime", "r1"), ("nat", None), ("self", "i-self")]
    assert calls[-1][0] == "self"  # self ALWAYS last


def test_reap_is_best_effort_and_continues_on_error():
    calls = []

    def boom(rid):
        raise RuntimeError("delete failed")

    r = RestrictedReaper(
        live_runtimes=lambda: ["r1"],
        delete_runtime=boom,
        delete_nat=lambda: calls.append("nat"),
        delete_self=lambda sid: calls.append("self"),
        self_instance_id="i",
    )
    errors = r.reap()
    assert len(errors) == 1 and "delete failed" in errors[0]
    # nat + self still ran despite the runtime error
    assert calls == ["nat", "self"]


def test_live_runtimes_from_ledger_excludes_deleted(tmp_path):
    led = ResourceLedger(tmp_path)
    led.record_created("run-1", "aliyun", "r1", "runtime")
    led.record_created("run-1", "aliyun", "r2", "runtime")
    led.mark_deleted("run-1", "r2")
    led.record_created("run-1", "aliyun", "nat-1", "nat")  # not a runtime
    assert live_runtimes_from_ledger(led) == ["r1"]
