"""Gated real-Docker SWE-bench smoke test (Task 6).

This test is decorated ``@pytest.mark.slow`` and is DESELECTED by the repo's
default pytest gate (``-m 'not live and not slow'``).  It is intentionally NOT
executed in this environment — Docker and the ``[swebench]`` extra are not
available.  Run it explicitly with::

    pytest -m slow tests/test_swe_bench_real_smoke.py

Prerequisites (must all be satisfied at runtime; otherwise the test is skipped):
- ``swebench`` package importable (``pip install clousight-bench[swebench]``)
- Docker daemon reachable (``docker info`` succeeds, ``docker`` binary on PATH)
- Network access to Docker Hub / SWE-bench fixtures

What it verifies:
- ``agent_kind="gold"`` → resolved == 1 (gold patches always apply)
- ``agent_kind="empty"`` → resolved == 0 (empty patch never applies)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Guards — skip early if prerequisites are missing
# ---------------------------------------------------------------------------

# 1. Require the [swebench] extra.  importorskip skips this entire module
#    when ``swebench`` is not installed, which is the normal CI case.
swebench = pytest.importorskip("swebench", reason="[swebench] extra not installed")

# 2. Require a reachable Docker daemon.
if not shutil.which("docker"):
    pytest.skip("docker binary not on PATH", allow_module_level=True)
try:
    subprocess.run(
        ["docker", "info"],
        check=True,
        capture_output=True,
        timeout=10,
    )
except Exception:
    pytest.skip("docker daemon not reachable", allow_module_level=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_one(agent_kind: str) -> dict:
    """Run SweBenchSuite on 1 instance with the given agent_kind; return parsed results.

    Exercises the real resolve→prepare→run chain so the smoke test is honest.
    """
    from clousight_bench.core.suite import DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()

    # Use the single bundled fixture instance for speed
    fixture_path = (
        Path(__file__).parent.parent / "src" / "clousight_bench" / "suites" / "swe_bench" / "fixtures"
    )
    instances = json.loads((fixture_path / "instances_subset.json").read_text())
    instance_id = instances[0]["instance_id"]

    target = Target(mode="endpoint", mock=False)
    driver = DriverContext(placement="local")

    # Resolve → prepare → run (the real chain; no hand-crafted EnvHandle)
    dataset = suite.resolve(
        {"instance_ids": [instance_id], "agent_kind": agent_kind},
        assets=None,
    )
    env = suite.prepare(target, dataset, driver)
    raw = suite.run(target, env, driver)
    results_path = raw.path("results")
    return json.loads(results_path.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_gold_agent_resolves_one() -> None:
    """gold agent on 1 instance → resolved == 1."""
    data = _run_one("gold")
    assert data.get("resolved") == 1, f"Expected resolved=1 for gold agent, got: {data}"
    assert data.get("total") == 1, f"Expected total=1, got: {data}"


@pytest.mark.slow
def test_empty_agent_resolves_zero() -> None:
    """empty agent on 1 instance → resolved == 0."""
    data = _run_one("empty")
    assert data.get("resolved") == 0, f"Expected resolved=0 for empty agent, got: {data}"
    assert data.get("total") == 1, f"Expected total=1, got: {data}"
