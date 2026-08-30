"""The stable public API surface: plugin authors import from clousight_bench /
clousight_bench.api, never from clousight_bench.core.*."""

from __future__ import annotations

import clousight_bench as cb
from clousight_bench import api


def test_headline_contracts_importable_from_top_level() -> None:
    for name in (
        "BenchmarkSuite",
        "Evaluator",
        "Metric",
        "MetricContext",
        "JudgeModel",
        "JudgeProvider",
        "Measurement",
        "ItemResult",
        "ItemScore",
        "ProviderAdapter",
        "DomainPack",
        "RunSpec",
        "ResultRecord",
    ):
        assert hasattr(cb, name), f"clousight_bench.{name} missing from public surface"


def test_api_all_is_exported_from_top_level() -> None:
    # everything api re-exports is reachable from the package root
    for name in api.__all__:
        assert hasattr(cb, name), f"api exports {name} but clousight_bench does not"
    assert set(api.__all__).issubset(set(cb.__all__))


def test_version_and_plugin_api_constants_public() -> None:
    assert cb.__version__ == cb.RUNNER_VERSION
    assert cb.RESULT_SCHEMA_VERSION == cb.SCHEMA_VERSION
    assert cb.PLUGIN_API_VERSION  # non-empty


def test_llm_common_is_public_and_backs_the_shipped_judge() -> None:
    # the shared LLM helpers are a supported (public, non-underscore) module
    from clousight_bench.suites import llm_common

    assert set(llm_common.__all__) >= {"EndpointJudge", "rows_to_items", "serving_measurements"}
    # the openai-compatible judge provider builds an EndpointJudge from it
    j = api.build_judge(
        {"provider": "openai-compatible", "endpoint": "https://api.example.com/v1", "model": "m"}
    )
    assert isinstance(j, llm_common.EndpointJudge)


def test_docstrings_present_on_contributor_contracts() -> None:
    for cls in (api.BenchmarkSuite, api.Evaluator, api.Metric, api.JudgeProvider):
        assert cls.__doc__ and len(cls.__doc__) > 40, f"{cls.__name__} needs a class docstring"
    # the abstract methods a suite author implements first are documented
    assert api.BenchmarkSuite.run.__doc__ and api.BenchmarkSuite.mock_artifacts.__doc__
    assert api.Evaluator.evaluate.__doc__
