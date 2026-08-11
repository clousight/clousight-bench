import pytest
from clousight_bench.domains.agent_runtime.aliyun import AliyunAgentRunTransport


class _FakeAdapter:
    def __init__(self):
        self.target = {"mock_base_url": "http://x"}
        self.run_id = None

    @property
    def mock_base_url(self):
        return self.target["mock_base_url"]


class _DictMemory:
    def __init__(self):
        self.data = {}

    def store(self, session_id, state):
        self.data[session_id] = dict(state)

    def fetch(self, session_id):
        return dict(self.data.get(session_id, {}))


def test_state_round_trip():
    t = AliyunAgentRunTransport(_FakeAdapter())
    t._memory = _DictMemory()
    t.persist_state("s1", {"k": "v"})
    assert t.load_state("s1") == {"k": "v"}


def test_register_tool_mcp_vs_others():
    t = AliyunAgentRunTransport(_FakeAdapter())
    t._mcp = type("M", (), {"activate": lambda self, name, spec: True})()
    assert t.register_tool("mcp", {"name": "demo"}) is True
    assert t.register_tool("native", {}) is False
    assert t.register_tool("openapi", {}) is False


def test_state_seam_default_is_live_gated():
    t = AliyunAgentRunTransport(_FakeAdapter())
    with pytest.raises(NotImplementedError):
        t.persist_state("s1", {"k": "v"})
