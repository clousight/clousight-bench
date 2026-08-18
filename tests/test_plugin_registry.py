"""Both built-in domain packs load via entry points and expose their tasks/adapters."""

import pytest

from clousight_bench.core.errors import AdapterNotRunnableError
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.plugin import RuntimeProviderPlugin
from clousight_bench.core.redaction import redact
from clousight_bench.core.registry import get_domain, load_domains
from clousight_bench.core.schema import RunSpec


def test_builtin_domains_discovered():
    domains = load_domains()
    assert "agent-runtime" in domains
    assert "bigdata-emr" in domains


def test_agent_runtime_surface():
    pack = get_domain("agent-runtime")
    assert "T1.3" in pack.tasks()
    adapters = pack.adapters()
    for name in ("local-sim", "aliyun-agentrun", "huawei-agentarts", "volcengine-agentkit"):
        assert name in adapters


def test_bigdata_surface():
    pack = get_domain("bigdata-emr")
    assert "J1.1" in pack.tasks()
    assert set(pack.adapters()) >= {"local-process", "aws-emr"}


def test_redact_scrubs_secrets():
    dirty = {"endpoint": "https://x", "access_key_secret": "SHHH", "nested": {"token": "T"}}
    clean = redact(dirty)
    assert clean["endpoint"] == "https://x"
    assert clean["access_key_secret"] == "<redacted>"
    assert clean["nested"]["token"] == "<redacted>"


def test_adapter_status_distinguishes_reference_from_skeleton():
    agent = get_domain("agent-runtime").adapters()
    bigdata = get_domain("bigdata-emr").adapters()

    assert agent["local-sim"].status == "reference"
    assert agent["local-sim"].provider is None
    assert agent["local-sim"].is_runnable()
    # aliyun-agentrun is experimental (its provider ran a live campaign), so it is
    # runnable; huawei/volcengine stay pure skeletons (rejected before preflight).
    assert agent["aliyun-agentrun"].status == "experimental"
    assert agent["aliyun-agentrun"].provider == "aliyun"
    assert agent["aliyun-agentrun"].is_runnable()
    assert agent["huawei-agentarts"].status == "skeleton"
    assert agent["huawei-agentarts"].provider == "huawei"
    assert not agent["huawei-agentarts"].is_runnable()
    assert agent["volcengine-agentkit"].status == "skeleton"
    assert agent["volcengine-agentkit"].provider == "volcengine"
    assert bigdata["local-process"].status == "reference"
    assert bigdata["local-process"].provider is None
    assert bigdata["aws-emr"].status == "skeleton"
    assert bigdata["aws-emr"].provider == "aws"


def test_orchestrator_rejects_skeleton_before_preflight(tmp_path):
    # Default (real) mode: a skeleton cloud is refused up front. aliyun-agentrun
    # is now provider-backed in the open core, so use a still-skeleton platform.
    with pytest.raises(AdapterNotRunnableError, match="huawei-agentarts.*skeleton"):
        execute(
            RunSpec("agent-runtime", "T1.3", "huawei-agentarts"),
            results_dir=tmp_path,
            preflight=False,
        )


def test_orchestrator_allows_skeleton_cloud_in_mock_mode(tmp_path):
    # The instance-level gate lets a skeleton cloud run end-to-end against the
    # simulated runtime (mode: mock) even though its class status is skeleton --
    # this exercises the full identity/endpoint/permission plumbing without an
    # account. Real mode stays gated (see the test above).
    rec = execute(
        RunSpec("agent-runtime", "T1.3", "aliyun-agentrun", target={"mode": "mock"}),
        results_dir=tmp_path,
        preflight=False,
    )
    assert rec.status == "completed"


def test_aliyun_runtime_provider_registered_in_open_core():
    # The Aliyun AgentRun provider is open-sourced in clousight-bench itself, so
    # its runtime provider is registered out of the box (this is what flips the
    # aliyun-agentrun skeleton to runnable in real mode). Other clouds (huawei /
    # volcengine) remain skeleton with no provider.
    from clousight_bench.core.registry import load_runtime_providers

    providers = load_runtime_providers()
    assert "aliyun" in providers
    assert "huawei" not in providers and "volcengine" not in providers


class _FakeTransport:
    """Minimal live transport a wired provider would return in real mode."""

    mock_base_url = None

    def start(self):
        pass

    def stop(self):
        pass

    def create_session(self, spec=None):
        return "wired-session"

    def destroy_session(self, session_id):
        pass


class _FakeAliyunProvider(RuntimeProviderPlugin):
    provider = "aliyun"

    def build_transport(self, adapter):
        return _FakeTransport()


def test_wired_provider_flips_real_mode_runnable(tmp_path, monkeypatch):
    # Registering a wired provider (what installing the commercial pack does)
    # makes a skeleton cloud runnable in real mode WITHOUT editing its adapter,
    # and its transport is what drives the run.
    import clousight_bench.core.registry as reg

    monkeypatch.setattr(
        reg,
        "get_runtime_provider",
        lambda provider: _FakeAliyunProvider() if provider == "aliyun" else None,
    )
    # Default (real) mode, previously refused as skeleton, now runs on the wired
    # transport -- completing proves it did NOT fall back to not-wired.
    rec = execute(
        RunSpec("agent-runtime", "T1.1", "aliyun-agentrun"),
        results_dir=tmp_path,
        preflight=False,
        allow_live=True,  # real-cloud run: acknowledge the live-run cost gate
    )
    assert rec.status == "completed"
