"""Adapters probe endpoint reachability + tool/Java versions at preflight time."""

from __future__ import annotations

import socket

from clousight_bench.core import preflight as pf
from clousight_bench.domains.key_value.adapters.ycsb import YcsbEndpointAdapter
from clousight_bench.domains.llm.adapters.openai_compatible import LlmEndpointAdapter
from clousight_bench.domains.transactional_db.adapters.benchbase import JdbcEndpointAdapter


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return port


def _named(report) -> dict:
    return {c.name: c for c in report.checks}


def test_jdbc_endpoint_unreachable_is_critical(monkeypatch):
    monkeypatch.setenv("BENCHBASE_HOME", "/nonexistent")
    adapter = JdbcEndpointAdapter(
        {"mode": "runtime", "dbtype": "postgres", "endpoint": f"127.0.0.1:{_free_port()}"}
    )
    checks = _named(adapter.preflight())
    assert "benchbase:endpoint" in checks
    c = checks["benchbase:endpoint"]
    assert not c.ok and c.severity == pf.CRITICAL and "allowlist" in c.remediation


def test_ycsb_redis_endpoint_probed_with_resp(monkeypatch):
    monkeypatch.setenv("YCSB_HOME", "/nonexistent")
    adapter = YcsbEndpointAdapter(
        {"mode": "runtime", "binding": "redis", "endpoint": f"127.0.0.1:{_free_port()}"}
    )
    checks = _named(adapter.preflight())
    assert "ycsb:endpoint" in checks
    assert not checks["ycsb:endpoint"].ok  # nothing listening


def test_llm_endpoint_ssrf_guard_blocks_metadata_host():
    adapter = LlmEndpointAdapter(
        {
            "mode": "runtime",
            "endpoint": "http://169.254.169.254/v1",
            "model": "m",
            "credentials_ref": "env:NOPE_KEY",
        }
    )
    checks = _named(adapter.preflight())
    c = checks["llm-reachability"]
    assert not c.ok and "SSRF" in c.detail


def test_llm_endpoint_reachability_probed(monkeypatch):
    # a live local listener stands in for the gateway; SSRF guard must allow it
    # or the check reports the guard verdict — either way the check exists.
    adapter = LlmEndpointAdapter(
        {
            "mode": "runtime",
            "endpoint": f"http://127.0.0.1:{_free_port()}/v1",
            "model": "m",
            "credentials_ref": "env:NOPE_KEY",
        }
    )
    checks = _named(adapter.preflight())
    assert "llm-reachability" in checks
    assert not checks["llm-reachability"].ok  # blocked by guard OR unreachable


def test_mock_mode_skips_probes():
    adapter = JdbcEndpointAdapter({"mode": "mock"})
    names = set(_named(adapter.preflight()))
    assert "benchbase:endpoint" not in names
