"""Provisioning lifecycle (T0.1 deploy / T0.2 teardown) at the adapter seam.

Mock mode makes the deploy/teardown observable deterministically with no
account (create->ready knob, clean/residual knobs); real mode without a wired
provider surfaces a clear not-wired error, never a false "not supported".
"""
import pytest

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.base import (
    DeprovisionResult,
    ProvisionResult,
)
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter


def _mock_adapter(**provision):
    a = AliyunAgentRunAdapter({"mode": "mock", "provision": provision})
    a.setup()
    return a


def test_mock_provision_returns_ready_runtime():
    a = _mock_adapter(ready_ms=0)
    try:
        r = a.provision({"artifact_ref": "zip://bench-agent"})
        assert isinstance(r, ProvisionResult)
        assert r.ready is True
        assert r.runtime_id
        assert r.artifact_ref == "zip://bench-agent"
        assert r.ready_latency_ms >= 0
        assert a.provision_status(r.runtime_id) == "ready"
    finally:
        a.teardown()


def test_mock_deprovision_is_clean_by_default():
    a = _mock_adapter()
    try:
        r = a.deprovision("sim-runtime-1")
        assert isinstance(r, DeprovisionResult)
        assert r.clean is True
        assert r.residual == []
    finally:
        a.teardown()


def test_mock_deprovision_surfaces_residual_leak():
    a = _mock_adapter(clean_teardown=False, residual_on_delete=["endpoint-x"])
    try:
        r = a.deprovision("sim-runtime-1")
        assert r.clean is False
        assert r.residual == ["endpoint-x"]
    finally:
        a.teardown()


def test_real_mode_provision_is_not_wired():
    # real mode (default), no wired provider registered -> the honest not-wired
    # seam, a clear actionable error rather than a false CapabilityNotSupported.
    a = AliyunAgentRunAdapter({})
    a.setup()
    try:
        with pytest.raises(NotImplementedError, match="not wired"):
            a.provision()
    finally:
        a.teardown()


# --- T0.1 / T0.2 end-to-end on the skeleton cloud in mock mode (no account) ---


def _mock_run(task_id, tmp_path, **provision):
    return execute(
        RunSpec(
            "agent-runtime", task_id, "aliyun-agentrun",
            target={"mode": "mock", "provision": provision},
        ),
        results_dir=tmp_path,
        preflight=False,
    )


def test_t0_1_provision_latency_end_to_end(tmp_path):
    rec = _mock_run("T0.1", tmp_path, ready_ms=0)
    assert rec.status == "completed"
    assert "provision_ready_ms" in rec.measurements
    assert rec.measurements["provision_ready"]["value"] is True


def test_t0_2_teardown_clean_end_to_end(tmp_path):
    rec = _mock_run("T0.2", tmp_path, clean_teardown=True)
    assert rec.status == "completed"
    assert rec.measurements["teardown_clean"]["value"] is True
    assert rec.measurements["residual_count"]["value"] == 0


def test_t0_2_teardown_residual_leak_is_a_finding(tmp_path):
    rec = _mock_run("T0.2", tmp_path, clean_teardown=False, residual_on_delete=["endpoint-x"])
    assert rec.status == "completed"
    assert rec.measurements["teardown_clean"]["value"] is False
    assert rec.measurements["residual_count"]["value"] == 1
    assert any(f["code"] == "agent_runtime.teardown_residual" for f in rec.findings)
