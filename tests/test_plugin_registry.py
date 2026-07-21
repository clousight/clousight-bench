"""Both built-in domain packs load via entry points and expose their tasks/adapters."""
from opencloudbench.core.plugin import _redact
from opencloudbench.core.registry import get_domain, load_domains


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
