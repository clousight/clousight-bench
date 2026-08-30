"""R5: provision/connect/destroy are separable — the provisioned-cloud machinery
(live-gate / cost / reaper) is gated by the explicit provisions_resources()
capability, so a connect-only run skips all of it even when execution is live."""

from __future__ import annotations

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.plugin import ProviderAdapter, ProvisionedCloudAdapter
from clousight_bench.core.schema import RunSpec
from clousight_bench.domains.agent_runtime.adapters.cn_clouds import AliyunAgentRunAdapter
from clousight_bench.domains.llm.adapters.openai_compatible import LlmEndpointAdapter, LlmMockAdapter

# --- the explicit capability --------------------------------------------------


class _LiveCloud(ProviderAdapter):
    name = "x-live-cloud"
    provider = "x"

    def execution_mode(self) -> str:
        return "live"

    def setup(self):  # pragma: no cover - not run here
        pass

    def teardown(self):  # pragma: no cover
        pass


class _ConnectOnly(ProviderAdapter):
    name = "x-connect"
    provider = None

    def execution_mode(self) -> str:
        return "live"  # connects to a live external service, but provisions nothing


class _Simulated(ProviderAdapter):
    name = "x-sim"
    provider = "x"

    def execution_mode(self) -> str:
        return "simulated"


class _AlwaysProvisions(ProvisionedCloudAdapter):
    name = "x-mixin"
    provider = None  # mixin declares True regardless of provider/mode


def test_default_derivation_of_provisions_resources() -> None:
    assert _LiveCloud().provisions_resources() is True  # live + provider
    assert _ConnectOnly().provisions_resources() is False  # no provider → connect-only
    assert _Simulated().provisions_resources() is False  # simulated → never provisions


def test_provisioned_cloud_mixin_declares_true() -> None:
    assert _AlwaysProvisions().provisions_resources() is True


def test_llm_adapters_are_explicitly_connect_only() -> None:
    assert LlmMockAdapter().provisions_resources() is False
    assert LlmEndpointAdapter({"endpoint": "https://x/v1", "model": "m"}).provisions_resources() is False


# --- the orchestrator gate is driven by the capability, not by live-ness ------


def test_connect_only_live_run_is_not_gated(tmp_path, monkeypatch) -> None:
    """The key R5 property: a run that executes LIVE but does not provision
    (provisions_resources() False) skips the live-gate — no --allow-live needed,
    no live.unconfirmed, no accidental block."""
    monkeypatch.setattr(AliyunAgentRunAdapter, "execution_mode", lambda self: "live")
    monkeypatch.setattr(AliyunAgentRunAdapter, "provisions_resources", lambda self: False)
    spec = RunSpec("agent-runtime", "stub.ok", "aliyun-agentrun", target={"mode": "mock"})
    rec = execute(spec, results_dir=tmp_path, preflight=False, allow_live=False)
    assert rec.status == "completed"  # NOT blocked despite live execution
    assert not any(f["code"] == "live.unconfirmed" for f in rec.findings)


def test_provisioning_live_run_still_gated(tmp_path, monkeypatch) -> None:
    """Control: a provisioning live run IS still gated (regression guard)."""
    monkeypatch.setattr(AliyunAgentRunAdapter, "execution_mode", lambda self: "live")
    monkeypatch.setattr(AliyunAgentRunAdapter, "provisions_resources", lambda self: True)
    spec = RunSpec("agent-runtime", "stub.ok", "aliyun-agentrun", target={"mode": "mock"})
    rec = execute(spec, results_dir=tmp_path, preflight=False, allow_live=False)
    assert rec.status == "invalid"
    assert any(f["code"] == "live.unconfirmed" for f in rec.findings)
