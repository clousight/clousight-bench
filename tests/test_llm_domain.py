"""The llm domain + OpenAI-compatible endpoint adapters."""

from __future__ import annotations

from clousight_bench.core import preflight as pf
from clousight_bench.core.registry import load_domains
from clousight_bench.domains.llm import LlmDomain
from clousight_bench.domains.llm.adapters.openai_compatible import LlmEndpointAdapter, LlmMockAdapter


def test_domain_loads_via_registry() -> None:
    domains = load_domains()
    assert "llm" in domains
    assert isinstance(domains["llm"], LlmDomain)


def test_domain_is_suite_first_no_tasks() -> None:
    # Single rail: domains no longer declare tasks at all.
    assert not hasattr(LlmDomain(), "tasks")


def test_domain_declares_both_platforms() -> None:
    assert set(LlmDomain().adapters()) == {"llm-mock", "llm-endpoint"}


def test_mock_is_a_simulated_reference() -> None:
    a = LlmMockAdapter()
    assert a.name == "llm-mock"
    assert a.status == "reference"
    assert a.execution_mode() == "simulated"


def test_endpoint_resolves_model_and_key(monkeypatch) -> None:
    monkeypatch.setenv("MY_LLM_KEY", "sk-test-123")
    a = LlmEndpointAdapter({"model": "qwen-max", "credentials_ref": "env:MY_LLM_KEY"})
    assert a.model() == "qwen-max"
    assert a.api_key() == "sk-test-123"
    assert a.status == "experimental"


def test_preflight_passes_in_mock() -> None:
    report = LlmEndpointAdapter({"mode": "mock"}).preflight()
    assert not [c for c in report.checks if not c.ok and c.severity == pf.CRITICAL]


def test_preflight_fails_loud_without_endpoint_or_key(monkeypatch) -> None:
    for var in ("OPENAI_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    report = LlmEndpointAdapter({"mode": "runtime"}).preflight()
    crit = {c.name for c in report.checks if not c.ok and c.severity == pf.CRITICAL}
    assert "llm-endpoint" in crit and "llm-credentials" in crit
