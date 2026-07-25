"""Both built-in domain packs load via entry points and expose their tasks/adapters."""
import pytest

from clousight_bench.core.errors import AdapterNotRunnableError
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.plugin import _redact
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
    clean = _redact(dirty)
    assert clean["endpoint"] == "https://x"
    assert clean["access_key_secret"] == "<redacted>"
    assert clean["nested"]["token"] == "<redacted>"


def test_adapter_status_distinguishes_reference_from_skeleton():
    agent = get_domain("agent-runtime").adapters()
    bigdata = get_domain("bigdata-emr").adapters()

    assert agent["local-sim"].status == "reference"
    assert agent["local-sim"].is_runnable()
    assert agent["aliyun-agentrun"].status == "skeleton"
    assert not agent["aliyun-agentrun"].is_runnable()
    assert bigdata["local-process"].status == "reference"
    assert bigdata["aws-emr"].status == "skeleton"


def test_orchestrator_rejects_skeleton_before_preflight(tmp_path):
    with pytest.raises(AdapterNotRunnableError, match="aliyun-agentrun.*skeleton"):
        execute(
            RunSpec("agent-runtime", "T1.3", "aliyun-agentrun"),
            results_dir=tmp_path,
            preflight=False,
        )
