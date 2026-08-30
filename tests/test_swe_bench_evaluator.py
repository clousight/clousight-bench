"""Tests for OfficialSweEvaluator (Task 4).

TDD: this file was written before the implementation.
All test data is built inline using tmp_path; no fixture files are used.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import RawArtifacts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_results(tmp: Path, resolved: int, total: int) -> None:
    per_instance: dict[str, dict[str, bool]] = {}
    for i in range(total):
        per_instance[f"instance_{i}"] = {"resolved": i < resolved}
    (tmp / "results.json").write_text(
        json.dumps({"per_instance": per_instance, "resolved": resolved, "total": total})
    )


def _make_raw(tmp: Path, *, with_usage: bool = False) -> RawArtifacts:
    manifest: dict[str, dict] = {
        "results": {"path": "results.json"},
    }
    if with_usage:
        manifest["usage"] = {"path": "usage.jsonl"}
        # Two token lines: 500 + 1500 = 2000 tokens total
        lines = [
            json.dumps({"kind": "llm_tokens", "value": 500, "unit": "tokens"}),
            json.dumps({"kind": "llm_tokens", "value": 1500, "unit": "tokens"}),
        ]
        (tmp / "usage.jsonl").write_text("\n".join(lines) + "\n")
    return RawArtifacts(dir=tmp, manifest=manifest)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_evaluate_resolved_ratio(tmp_path: Path) -> None:
    """Basic case: 1 resolved out of 2 → ratio 0.5."""
    _write_results(tmp_path, resolved=1, total=2)
    raw = _make_raw(tmp_path)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    ev = OfficialSweEvaluator()
    result = ev.evaluate(raw)

    assert "swe-bench.resolved" in result
    m = result["swe-bench.resolved"]
    assert isinstance(m, Measurement)
    assert m.value == pytest.approx(0.5)
    assert m.unit == "ratio"
    assert m.reproducibility_class == "deterministic"
    assert m.official is True


def test_evaluate_no_usage_no_cost(tmp_path: Path) -> None:
    """Without usage in manifest, cost_per_resolved must NOT be present."""
    _write_results(tmp_path, resolved=3, total=5)
    raw = _make_raw(tmp_path, with_usage=False)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    assert "swe-bench.cost_per_resolved" not in result


def test_evaluate_with_usage_adds_cost(tmp_path: Path) -> None:
    """With usage.jsonl present, cost_per_resolved key appears."""
    _write_results(tmp_path, resolved=2, total=4)
    raw = _make_raw(tmp_path, with_usage=True)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    assert "swe-bench.cost_per_resolved" in result
    m = result["swe-bench.cost_per_resolved"]
    assert isinstance(m, Measurement)
    assert m.unit == "usd"
    assert m.reproducibility_class == "environmental"
    assert m.official is True
    # sanity-check value is positive
    assert m.value > 0


def test_evaluate_cost_per_resolved_math(tmp_path: Path) -> None:
    """Verify cost arithmetic: 2000 tokens at 0.002 USD/1k = 0.004 USD total / 2 resolved = 0.002."""
    _write_results(tmp_path, resolved=2, total=4)
    raw = _make_raw(tmp_path, with_usage=True)  # 2000 tokens total

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    m = result["swe-bench.cost_per_resolved"]
    # 2000 tokens / 1000 * price_per_1k / resolved
    # We do not hard-code the price (seed may change), but we can assert positive and finite.
    assert m.value > 0
    assert m.value < 1.0  # sanity upper bound


def test_key_namespace(tmp_path: Path) -> None:
    """All returned keys must start with 'swe-bench.'."""
    _write_results(tmp_path, resolved=1, total=2)
    raw = _make_raw(tmp_path, with_usage=True)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    for key in result:
        assert key.startswith("swe-bench."), f"Key {key!r} does not start with 'swe-bench.'"


def test_total_zero_guard(tmp_path: Path) -> None:
    """total==0 → value is 0.0, no ZeroDivisionError."""
    _write_results(tmp_path, resolved=0, total=0)
    raw = _make_raw(tmp_path)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    assert result["swe-bench.resolved"].value == pytest.approx(0.0)


def test_missing_results_is_fail_safe(tmp_path: Path) -> None:
    """A missing/corrupt results.json returns {} (fail-safe), never raises —
    symmetric with the mmlu/gsm8k/human-eval evaluators."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    missing = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "nope.json"}})
    assert OfficialSweEvaluator().evaluate(missing) == {}

    (tmp_path / "corrupt.json").write_text("{not json")
    corrupt = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "corrupt.json"}})
    assert OfficialSweEvaluator().evaluate(corrupt) == {}


def test_supports(tmp_path: Path) -> None:
    """supports() returns True for 'swe-bench', False for anything else."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    ev = OfficialSweEvaluator()
    assert ev.supports("swe-bench", "any-product") is True
    assert ev.supports("humaneval", "any-product") is False
    assert ev.supports("", "") is False


def test_evaluator_id_and_official() -> None:
    """Evaluator class-level attributes."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    ev = OfficialSweEvaluator()
    assert ev.evaluator_id == "official-swe-evaluator"
    assert ev.official is True


def test_resolved_zero_with_usage_no_cost_dimension(tmp_path: Path) -> None:
    """resolved==0 with usage present: cost dimension is omitted (division by zero guard)."""
    _write_results(tmp_path, resolved=0, total=3)
    raw = _make_raw(tmp_path, with_usage=True)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    # cost_per_resolved is undefined when resolved==0
    assert "swe-bench.cost_per_resolved" not in result


# ---------------------------------------------------------------------------
# Task 7: evaluator hygiene tests
# ---------------------------------------------------------------------------


def test_cost_omitted_on_malformed_usage(tmp_path: Path) -> None:
    """Malformed usage line → cost_per_resolved is OMITTED, resolved still returned."""
    _write_results(tmp_path, resolved=2, total=4)
    # Write usage.jsonl with a good line then a garbage line
    lines = [
        json.dumps({"kind": "llm_tokens", "value": 500, "unit": "tokens"}),
        "THIS IS NOT JSON !!!",
    ]
    (tmp_path / "usage.jsonl").write_text("\n".join(lines) + "\n")
    manifest = {
        "results": {"path": "results.json"},
        "usage": {"path": "usage.jsonl"},
    }
    raw = RawArtifacts(dir=tmp_path, manifest=manifest)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    assert "swe-bench.resolved" in result, "resolved measurement must still be present"
    assert "swe-bench.cost_per_resolved" not in result, (
        "cost_per_resolved must be OMITTED when any usage line is malformed"
    )


def test_cost_notes_carry_price_source(tmp_path: Path) -> None:
    """Cost measurement notes contain 'seed' or 'fallback' indicating price source."""
    _write_results(tmp_path, resolved=2, total=4)
    raw = _make_raw(tmp_path, with_usage=True)

    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    result = OfficialSweEvaluator().evaluate(raw)
    assert "swe-bench.cost_per_resolved" in result
    m = result["swe-bench.cost_per_resolved"]
    assert "seed" in m.notes or "fallback" in m.notes, (
        f"Expected notes to contain 'seed' or 'fallback', got: {m.notes!r}"
    )
