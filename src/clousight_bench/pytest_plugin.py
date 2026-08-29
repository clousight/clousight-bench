"""Pytest plugin: run a Clousight Bench suite as a native pytest assertion.

Registered via the ``pytest11`` entry point, so ``pip install clousight-bench``
makes it available with no conftest wiring. The point is CI adoption: running a
benchmark is normally a low-frequency manual operation, but as a pytest test it
drops into an enterprise CI pipeline as a red/green gate.

Usage — call ``assert_run`` inside any pytest test (or via the ``clousight``
fixture); it runs one suite and raises ``AssertionError`` unless the run
completes and every threshold is met:

    from clousight_bench.pytest_plugin import assert_run

    def test_our_llm_endpoint_mmlu_gate():
        assert_run(
            domain="llm", suite="mmlu", platform="llm-endpoint",
            target={"endpoint": "...", "model": "qwen-max",
                    "credentials_ref": "env:DASHSCOPE_API_KEY"},
            params={"limit": 100},
            thresholds={"mmlu.accuracy": {"min": 0.75},
                        "mmlu.avg_latency_ms": {"max": 3000}},
        )

Thresholds use the shared ``core.thresholds`` model (min/max; scalar == min).
For non-pytest CI, ``csbench run --assert <thresholds.yaml>`` gates via exit code.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from clousight_bench.core.thresholds import check_thresholds


def assert_run(
    *,
    domain: str,
    suite: str,
    platform: str,
    target: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    results_dir: str | Path | None = None,
    preflight: bool = True,
) -> Any:
    """Run one suite and assert it completed + met every threshold; return the record.

    ``target`` defaults to ``{"mode": "mock"}`` (the offline path). Raises
    ``AssertionError`` (so pytest reports a clean failure) when the run does not
    reach ``completed`` or any threshold is unmet.
    """
    from clousight_bench.core import orchestrator
    from clousight_bench.core.schema import RunSpec

    spec = RunSpec(
        domain=domain,
        task_id=f"suite:{suite}",
        platform=platform,
        target=dict(target) if target is not None else {"mode": "mock"},
        params=dict(params) if params is not None else {},
    )
    rdir = Path(results_dir) if results_dir is not None else Path(tempfile.mkdtemp(prefix="csbench-pytest-"))
    record = orchestrator.execute(spec, results_dir=rdir, enrich=False, preflight=preflight)

    if record.status != "completed":
        raise AssertionError(
            f"clousight suite:{suite} did not complete — status={record.status}; errors={record.errors}"
        )
    failures = check_thresholds(record.measurements, thresholds or {})
    if failures:
        raise AssertionError(f"clousight suite:{suite} threshold(s) not met:\n  " + "\n  ".join(failures))
    return record


@pytest.fixture
def clousight():
    """Fixture yielding :func:`assert_run` — ``def test_x(clousight): clousight(...)``."""
    return assert_run


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "clousight: marks a test that gates on a Clousight Bench suite run (see assert_run).",
    )
