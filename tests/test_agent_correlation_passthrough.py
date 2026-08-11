"""Test that handle_invoke passes _correlation_id to the mock tool via header.

Task 3/8 of the reliability-group-redesign: correlation-id transparent passthrough.
The mock tool server buckets calls by (target, corr) in ToolState.call_counts with
keys of the form "target|corr". This test proves the agent writes the
X-Clousight-Correlation-Id header so the mock can produce the per-corr bucket.
"""
from threading import Thread

from clousight_bench.domains.agent_runtime.agent_bundle import agent as agent_mod
from clousight_bench.domains.agent_runtime.mock_tools import make_server


def test_handle_invoke_passes_correlation_id_to_tool():
    srv, state = make_server(0)
    Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        agent_mod.handle_invoke(
            {
                "tool": {"target": "prices", "method": "GET"},
                "mock_base_url": base,
                "_correlation_id": "corr-xyz",
            }
        )
        assert "prices|corr-xyz" in state.call_counts
    finally:
        srv.shutdown()
