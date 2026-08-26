"""Conformance checks for suite evaluators (Task 6).

Tests that:
1. The official SWE-bench evaluator PASSES the namespace/official check when
   evaluated against the suite's mock_artifacts.
2. A synthetic evaluator that emits an official=True key OUTSIDE the suite
   namespace FAILS the check (returns a failed CheckResult).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clousight_bench.core.conformance import check_evaluator
from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import RawArtifacts

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _write_results(tmp: Path, resolved: int = 1, total: int = 2) -> None:
    per_instance = {f"inst_{i}": {"resolved": i < resolved} for i in range(total)}
    (tmp / "results.json").write_text(
        json.dumps({"per_instance": per_instance, "resolved": resolved, "total": total})
    )


def _make_raw(tmp: Path) -> RawArtifacts:
    return RawArtifacts(
        dir=tmp,
        manifest={"results": {"path": "results.json"}},
    )


# ---------------------------------------------------------------------------
# Test 1: official SWE-bench evaluator PASSES conformance
# ---------------------------------------------------------------------------


def test_official_swe_evaluator_passes_conformance(tmp_path: Path) -> None:
    """OfficialSweEvaluator output passes check_evaluator for suite 'swe-bench'."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    _write_results(tmp_path, resolved=1, total=2)
    raw = _make_raw(tmp_path)
    ev = OfficialSweEvaluator()
    measurements = ev.evaluate(raw)

    results = check_evaluator(ev, "swe-bench", measurements)
    failed = [r for r in results if not r.ok]
    assert not failed, f"Official evaluator failed conformance: {failed}"


def test_official_evaluator_conformance_with_cost(tmp_path: Path) -> None:
    """OfficialSweEvaluator with cost_per_resolved also passes."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    _write_results(tmp_path, resolved=1, total=2)
    usage_lines = [
        json.dumps({"kind": "llm_tokens", "value": 1000, "unit": "tokens"}),
    ]
    (tmp_path / "usage.jsonl").write_text("\n".join(usage_lines) + "\n")
    raw = RawArtifacts(
        dir=tmp_path,
        manifest={
            "results": {"path": "results.json"},
            "usage": {"path": "usage.jsonl"},
        },
    )
    ev = OfficialSweEvaluator()
    measurements = ev.evaluate(raw)

    # Both swe-bench.resolved and swe-bench.cost_per_resolved are present
    assert "swe-bench.resolved" in measurements
    assert "swe-bench.cost_per_resolved" in measurements

    results = check_evaluator(ev, "swe-bench", measurements)
    failed_checks = [r for r in results if not r.ok]
    assert not failed_checks, f"Official evaluator (with cost) failed conformance: {failed_checks}"


# ---------------------------------------------------------------------------
# Test 2: synthetic evaluator with wrong namespace FAILS conformance
# ---------------------------------------------------------------------------


class _BadNamespaceEvaluator:
    """Fake OFFICIAL evaluator that emits a key outside the suite namespace."""

    evaluator_id = "bad-ns-evaluator"
    official = True

    def evaluate(self, raw: Any) -> dict[str, Measurement]:
        return {
            "swe-bench.resolved": Measurement(
                value=0.5, unit="ratio", reproducibility_class="deterministic", official=True
            ),
            # This key is OUTSIDE the "swe-bench." namespace — a violation.
            "other-suite.some_metric": Measurement(
                value=1.0, unit="count", reproducibility_class="deterministic", official=True
            ),
        }


def test_synthetic_evaluator_with_wrong_namespace_fails(tmp_path: Path) -> None:
    """An official evaluator emitting a key outside the suite namespace fails check."""
    ev = _BadNamespaceEvaluator()
    measurements = ev.evaluate(None)

    results = check_evaluator(ev, "swe-bench", measurements)
    ns_check = next(r for r in results if r.name == "evaluator:namespace")
    assert ns_check.ok is False, "Expected namespace check to fail for out-of-namespace key"
    assert "other-suite.some_metric" in ns_check.detail


# ---------------------------------------------------------------------------
# Test 3: custom evaluator (official=False) must use its own namespace
# ---------------------------------------------------------------------------


class _CustomEvaluator:
    """Fake CUSTOM evaluator that correctly emits under its own namespace."""

    evaluator_id = "my-custom-evaluator"
    official = False

    def evaluate(self, raw: Any) -> dict[str, Measurement]:
        return {
            "my-custom-evaluator.score": Measurement(
                value=42.0, unit="points", reproducibility_class="deterministic", official=False
            ),
        }


def test_custom_evaluator_passes_conformance(tmp_path: Path) -> None:
    """A custom evaluator emitting only official=False keys under its own namespace passes."""
    ev = _CustomEvaluator()
    measurements = ev.evaluate(None)

    results = check_evaluator(ev, "swe-bench", measurements)
    failed = [r for r in results if not r.ok]
    assert not failed, f"Custom evaluator failed conformance: {failed}"


class _CustomEvaluatorBadFlag:
    """Fake CUSTOM evaluator that incorrectly marks a key as official=True."""

    evaluator_id = "my-custom-evaluator"
    official = False

    def evaluate(self, raw: Any) -> dict[str, Measurement]:
        return {
            "my-custom-evaluator.score": Measurement(
                value=1.0,
                unit="ratio",
                reproducibility_class="deterministic",
                official=True,  # Wrong! Custom evaluators must be official=False
            ),
        }


def test_custom_evaluator_with_official_flag_fails(tmp_path: Path) -> None:
    """A custom evaluator emitting official=True measurements fails the flag check."""
    ev = _CustomEvaluatorBadFlag()
    measurements = ev.evaluate(None)

    results = check_evaluator(ev, "swe-bench", measurements)
    flag_check = next(r for r in results if r.name == "evaluator:official-flag")
    assert flag_check.ok is False, (
        "Expected official-flag check to fail for custom evaluator with official=True"
    )
    assert "my-custom-evaluator.score" in flag_check.detail


# ---------------------------------------------------------------------------
# Test: anti-namespace-squatting check (Task 7)
# ---------------------------------------------------------------------------


class _SquattingEvaluator:
    """Fake CUSTOM evaluator (official=False) that squats on a known suite namespace."""

    evaluator_id = "my-eval"
    official = False

    def __init__(self, key: str) -> None:
        self._key = key

    def evaluate(self, raw: Any) -> dict[str, Measurement]:
        return {
            self._key: Measurement(
                value=0.9,
                unit="ratio",
                reproducibility_class="deterministic",
                official=False,
            )
        }


def test_check_evaluator_suite_squatting_fails() -> None:
    """Custom evaluator emitting a key under a known suite's namespace fails squatting check."""
    ev = _SquattingEvaluator("swe-bench.score")
    measurements = ev.evaluate(None)

    results = check_evaluator(ev, "swe-bench", measurements, known_suite_ids=["swe-bench"])
    squatting_check = next(r for r in results if r.name == "evaluator:no-suite-squatting")
    assert squatting_check.ok is False, "Expected squatting check to fail"
    assert "swe-bench" in squatting_check.detail


def test_check_evaluator_no_squatting_own_namespace_passes() -> None:
    """Custom evaluator emitting under its own namespace passes the squatting check."""
    ev = _SquattingEvaluator("my-eval.score")
    measurements = ev.evaluate(None)

    results = check_evaluator(ev, "swe-bench", measurements, known_suite_ids=["swe-bench"])
    squatting_check = next(r for r in results if r.name == "evaluator:no-suite-squatting")
    assert squatting_check.ok is True, f"Expected squatting check to pass, got: {squatting_check}"


def test_check_evaluator_official_gets_passing_squatting_check() -> None:
    """Official evaluators always get a passing no-suite-squatting CheckResult."""
    import json
    import tempfile

    from clousight_bench.core.suite import RawArtifacts
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "results.json").write_text(json.dumps({"resolved": 1, "total": 2, "per_instance": {}}))
        raw = RawArtifacts(dir=p, manifest={"results": {"path": "results.json"}})
        ev = OfficialSweEvaluator()
        measurements = ev.evaluate(raw)
        results = check_evaluator(ev, "swe-bench", measurements, known_suite_ids=["swe-bench"])
        squatting_check = next(r for r in results if r.name == "evaluator:no-suite-squatting")
        assert squatting_check.ok is True, (
            f"Official evaluator should always pass squatting check: {squatting_check}"
        )
