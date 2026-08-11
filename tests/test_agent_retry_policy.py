"""Tests for the pinned 5xx-retry policy on the benchmark agent.

The policy is a fixed contract (not parameterised):
  max_retries=2  → 3 total attempts on persistent 5xx
  backoff_ms=200 → skipped via monkeypatch in tests
  retry_on=5xx   → 4xx and connection failures (599) do NOT retry
"""

import pytest

pytest.importorskip("langchain")
from clousight_bench.domains.agent_runtime.agent_bundle import lc_agent


def test_retry_policy_constant():
    assert lc_agent.AGENT_RETRY_POLICY == {
        "max_retries": 2,
        "backoff_ms": 200,
        "retry_on": "5xx",
    }


def test_agent_retries_twice_on_5xx_then_gives_up(monkeypatch):
    """Persistent 5xx → tool called 3 times (1 initial + 2 retries)."""
    monkeypatch.setattr(lc_agent.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(self, **kwargs):
        calls["n"] += 1
        return '{"_tool_http_status": 500, "error": "boom"}'

    monkeypatch.setattr(lc_agent.MockServerTool, "_run", fake_run)
    lc_agent.run(
        {
            "tool": {"target": "prices", "method": "GET"},
            "mock_base_url": "http://x",
            "mock_token": "",
        }
    )
    assert calls["n"] == 3  # 1 + 2 retries


def test_agent_no_retry_on_success(monkeypatch):
    """200 response → tool called exactly once."""
    monkeypatch.setattr(lc_agent.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(self, **kwargs):
        calls["n"] += 1
        return '{"_tool_http_status": 200, "products": []}'

    monkeypatch.setattr(lc_agent.MockServerTool, "_run", fake_run)
    lc_agent.run(
        {
            "tool": {"target": "prices", "method": "GET"},
            "mock_base_url": "http://x",
            "mock_token": "",
        }
    )
    assert calls["n"] == 1


def test_agent_no_retry_on_4xx(monkeypatch):
    """4xx response → tool called exactly once (no retry)."""
    monkeypatch.setattr(lc_agent.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(self, **kwargs):
        calls["n"] += 1
        return '{"_tool_http_status": 404, "error": "nope"}'

    monkeypatch.setattr(lc_agent.MockServerTool, "_run", fake_run)
    lc_agent.run(
        {
            "tool": {"target": "prices", "method": "GET"},
            "mock_base_url": "http://x",
            "mock_token": "",
        }
    )
    assert calls["n"] == 1


def test_agent_no_retry_on_connection_failure(monkeypatch):
    """Connection failure (599) → tool called exactly once (no retry)."""
    monkeypatch.setattr(lc_agent.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(self, **kwargs):
        calls["n"] += 1
        return '{"_tool_http_status": 599, "error": "connection refused"}'

    monkeypatch.setattr(lc_agent.MockServerTool, "_run", fake_run)
    lc_agent.run(
        {
            "tool": {"target": "prices", "method": "GET"},
            "mock_base_url": "http://x",
            "mock_token": "",
        }
    )
    assert calls["n"] == 1


def test_agent_retries_then_succeeds(monkeypatch):
    """5xx on first attempt, 200 on second → tool called twice, not three times."""
    monkeypatch.setattr(lc_agent.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"_tool_http_status": 500, "error": "transient"}'
        return '{"_tool_http_status": 200, "products": []}'

    monkeypatch.setattr(lc_agent.MockServerTool, "_run", fake_run)
    lc_agent.run(
        {
            "tool": {"target": "prices", "method": "GET"},
            "mock_base_url": "http://x",
            "mock_token": "",
        }
    )
    assert calls["n"] == 2
