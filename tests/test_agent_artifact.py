"""The benchmark agent artifact really calls the pinned tool universe.

No cloud: starts the mock tool universe in-process and drives the agent's
invoke core against it, proving the deployable payload makes the requested tool
call and reports the tool's own HTTP outcome.
"""
import importlib


def _agent():
    return importlib.import_module("clousight_bench.domains.agent_runtime.agent_bundle.agent")


def _mock_universe():
    from clousight_bench.domains.agent_runtime.mock_tools import start_in_thread

    server, _ = start_in_thread(0)
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"


def test_agent_invoke_calls_the_tool_and_reports_status():
    agent = _agent()
    server, base = _mock_universe()
    try:
        r = agent.handle_invoke(
            {"tool": {"target": "prices", "method": "GET"}, "mock_base_url": base}
        )
        assert r["ok"] is True
        assert r["status"] == 200
        assert r["tool_target"] == "prices"
    finally:
        server.shutdown()


def test_agent_reports_failure_when_tool_unreachable():
    # An unreachable tool endpoint -> the agent surfaces a non-2xx outcome
    # (transport-level failure), never masks it as success and never retries.
    agent = _agent()
    r = agent.handle_invoke(
        {"tool": {"target": "prices", "method": "GET"}, "mock_base_url": "http://127.0.0.1:1"}
    )
    assert r["ok"] is False
    assert r["tool_target"] == "prices"
