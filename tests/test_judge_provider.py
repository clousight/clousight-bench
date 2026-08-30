"""R3b: the clousight_bench.judges seam — JudgeProvider registry + build_judge."""

from __future__ import annotations

import pytest

from clousight_bench.core.judge import JudgeModel
from clousight_bench.core.registry import RegistryError, build_judge, load_judge_providers
from clousight_bench.judges.openai_compatible import OpenAiCompatibleJudgeProvider


def test_openai_compatible_provider_registered() -> None:
    providers = load_judge_providers()
    assert "openai-compatible" in providers
    assert isinstance(providers["openai-compatible"], OpenAiCompatibleJudgeProvider)


def test_build_judge_selects_provider_and_builds_endpoint_judge() -> None:
    j = build_judge(
        {"provider": "openai-compatible", "endpoint": "https://api.example.com/v1", "model": "qwen-max"}
    )
    assert isinstance(j, JudgeModel)
    assert j.model_id() == "qwen-max"


def test_build_judge_none_when_unconfigured() -> None:
    assert build_judge(None) is None
    assert build_judge({}) is None
    assert build_judge({"endpoint": "x"}) is None  # no provider → not a judge run


def test_build_judge_unknown_provider_fails_loud() -> None:
    with pytest.raises(RegistryError, match="unknown judge provider"):
        build_judge({"provider": "acme-secret-judge", "endpoint": "https://x/v1", "model": "m"})


def test_provider_requires_endpoint_and_model() -> None:
    p = OpenAiCompatibleJudgeProvider()
    with pytest.raises(RuntimeError, match="endpoint.*model"):
        p.build({"model": "m"})
    with pytest.raises(RuntimeError, match="endpoint.*model"):
        p.build({"endpoint": "https://x/v1"})


def test_provider_ssrf_guard_on_build() -> None:
    """build() constructs an EndpointJudge, which SSRF-validates the endpoint."""
    p = OpenAiCompatibleJudgeProvider()
    with pytest.raises(RuntimeError):
        p.build({"provider": "openai-compatible", "endpoint": "http://169.254.169.254/v1", "model": "m"})


def test_provider_resolves_env_credentials_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_JUDGE_KEY", "sk-judge")
    j = build_judge(
        {
            "provider": "openai-compatible",
            "endpoint": "https://api.example.com/v1",
            "model": "m",
            "credentials_ref": "env:MY_JUDGE_KEY",
        }
    )
    # key is resolved from the env var (never an inline secret); stored on the judge
    assert j is not None and j._api_key == "sk-judge"  # noqa: SLF001 - white-box check
