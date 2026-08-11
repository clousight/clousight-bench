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


def test_register_tool_dispatches_by_path(monkeypatch):
    # register_tool routes mcp/native to their (real, control-plane) handlers and
    # rejects openapi, which AgentRun does not support.
    t = AliyunAgentRunTransport(_FakeAdapter())
    monkeypatch.setattr(t, "_register_tool_mcp", lambda: True)
    monkeypatch.setattr(t, "_register_tool_native", lambda: False)
    assert t.register_tool("mcp", {"name": "demo"}) is True
    assert t.register_tool("native", {}) is False
    assert t.register_tool("openapi", {}) is False
