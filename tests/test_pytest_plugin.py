"""The clousight-bench pytest plugin (assert_run + fixture + threshold gate).

Uses pytest's ``pytester`` to run an inner pytest session, proving the plugin is
discovered via the ``pytest11`` entry point and that a suite run gates a real
pytest test red/green.
"""

from __future__ import annotations

import pytest

from clousight_bench.pytest_plugin import assert_run

pytest_plugins = ["pytester"]


def test_assert_run_passes_on_met_threshold() -> None:
    record = assert_run(
        domain="llm",
        suite="mmlu",
        platform="llm-mock",
        thresholds={"mmlu.accuracy": {"min": 0.9}},
    )
    assert record.status == "completed"
    assert record.measurements["mmlu.accuracy"]["value"] == 1.0


def test_assert_run_raises_on_unmet_threshold() -> None:
    with pytest.raises(AssertionError, match="threshold"):
        assert_run(
            domain="llm",
            suite="mmlu",
            platform="llm-mock",
            thresholds={"mmlu.avg_latency_ms": {"max": 0.0001}},
        )


def test_assert_run_raises_on_missing_measurement() -> None:
    with pytest.raises(AssertionError, match="not measured"):
        assert_run(
            domain="llm",
            suite="mmlu",
            platform="llm-mock",
            thresholds={"nonexistent.metric": {"min": 1}},
        )


def test_plugin_is_discovered_and_gates_a_real_pytest(pytester: pytest.Pytester) -> None:
    # The plugin auto-loads (pytest11 entry point); the `clousight` fixture and
    # assert_run drive a real pytest test that goes red on an unmet threshold.
    pytester.makepyfile(
        """
        def test_gate_pass(clousight):
            clousight(domain="llm", suite="mmlu", platform="llm-mock",
                      thresholds={"mmlu.accuracy": {"min": 0.5}})

        def test_gate_fail(clousight):
            clousight(domain="llm", suite="mmlu", platform="llm-mock",
                      thresholds={"mmlu.accuracy": {"min": 1.5}})
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1, failed=1)


def test_marker_registered(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest_subprocess("--markers")
    result.stdout.fnmatch_lines(["*clousight:*Clousight Bench suite*"])
