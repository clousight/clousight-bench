"""The wired Aliyun runtime provider: registration, seam, and honest SDK gating.

Runs WITHOUT an account -- it proves the open-core seam is wired (provider
discovered, real mode runnable, wired transport built) and that a real call with
the SDK missing fails with a clear install hint. The live request/response
plumbing is validated against a real account as a separate step.
"""
import sys

import pytest
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport, AliyunRuntimeProvider
from clousight_bench.core.registry import get_runtime_provider
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter


def test_provider_registered_via_entry_point():
    p = get_runtime_provider("aliyun")
    assert isinstance(p, AliyunRuntimeProvider)
    assert p.provider == "aliyun"


def test_real_mode_runnable_and_builds_wired_transport():
    # With the pack installed, the skeleton cloud is runnable in real mode and
    # builds the wired transport -- NOT the not-wired fallback.
    a = AliyunAgentRunAdapter({"region": "cn-hangzhou"})  # real mode is the default
    assert a.is_runnable_instance() is True
    assert isinstance(a._build_transport(), AliyunAgentRunTransport)


def test_real_call_without_sdk_gives_install_hint(monkeypatch):
    # Force the SDK import to fail (it may be installed via the `aliyun` extra) so
    # this deterministically exercises the missing-SDK path: a clear, actionable
    # install hint, never an obscure ImportError or a false CapabilityNotSupported.
    monkeypatch.setitem(sys.modules, "alibabacloud_agentrun20250910.client", None)
    a = AliyunAgentRunAdapter({"region": "cn-hangzhou"})
    a.setup()
    try:
        with pytest.raises(RuntimeError, match="pip install alibabacloud-agentrun"):
            a.provision({"artifact_ref": "oss://bucket/agent.zip"})
    finally:
        a.teardown()


def test_control_plane_request_is_a_valid_sdk_model():
    # The provision request is built from the real SDK's typed models and passes
    # the SDK's own validate() -- no dicts, no blind shapes. artifact_ref splits
    # into OSS bucket + object; the wire shape is camelCase as the API expects.
    a = AliyunAgentRunAdapter({"region": "cn-hangzhou", "runtime_name": "csb"})
    req = AliyunAgentRunTransport(a)._create_runtime_request(
        {"artifact_ref": "oss://my-bucket/agents/agent.zip"}
    )
    req.validate()  # raises if the model is malformed
    code = req.to_map()["body"]["codeConfiguration"]
    assert code["ossBucketName"] == "my-bucket"
    assert code["ossObjectName"] == "agents/agent.zip"


def test_provision_request_tags_the_run_id():
    # The orchestrator sets adapter.run_id; the request carries it as a system tag
    # so a later billing reconciliation can attribute actual cost to the run.
    a = AliyunAgentRunAdapter({"region": "cn-hangzhou"})
    a.run_id = "run-20260730-000000-abcdef"
    req = AliyunAgentRunTransport(a)._create_runtime_request({"artifact_ref": "oss://b/o.zip"})
    assert req.body.system_tags == ["clousight-run-id:run-20260730-000000-abcdef"]


def test_provision_request_omits_tag_without_run_id():
    a = AliyunAgentRunAdapter({"region": "cn-hangzhou"})  # no run_id (e.g. outside a run)
    req = AliyunAgentRunTransport(a)._create_runtime_request({"artifact_ref": "oss://b/o.zip"})
    assert req.body.system_tags is None


def test_data_plane_ops_are_gated_until_live():
    # Until validated on a live account, the data plane's real seams fail loudly
    # and clearly rather than calling a wrong/absent SDK op. (A non-empty plan is
    # needed so the invoke seam actually fires.)
    from clousight_bench.domains.agent_runtime.aliyun import _DataPlaneNotWired
    from clousight_bench.domains.agent_runtime.adapters.base import ToolCall

    t = AliyunAgentRunTransport(AliyunAgentRunAdapter({"region": "cn-hangzhou"}))
    with pytest.raises(_DataPlaneNotWired):
        t.run_tool_plan("s", [ToolCall(target="prices")])
    with pytest.raises(_DataPlaneNotWired):
        t.persist_state("s", {"k": "v"})


def test_mock_mode_ignores_wired_provider():
    # mode: mock still uses the in-process simulator regardless of the wired
    # provider, so provisioning is exercisable with no account.
    a = AliyunAgentRunAdapter({"mode": "mock"})
    a.setup()
    try:
        assert a.provision({"artifact_ref": "x"}).ready is True
    finally:
        a.teardown()
