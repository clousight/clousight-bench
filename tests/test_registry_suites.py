from clousight_bench.core.registry import (
    BENCHMARK_SUITE_ENTRY_POINT_GROUP,
    EVALUATOR_ENTRY_POINT_GROUP,
    load_benchmark_suites,
    load_evaluators,
)


def test_entry_point_group_names():
    assert BENCHMARK_SUITE_ENTRY_POINT_GROUP == "clousight_bench.benchmark_suites"
    assert EVALUATOR_ENTRY_POINT_GROUP == "clousight_bench.evaluators"


def test_loaders_return_containers():
    assert isinstance(load_benchmark_suites(), dict)
    assert isinstance(load_evaluators(), list)
