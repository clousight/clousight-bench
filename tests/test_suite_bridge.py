"""Tests for the suite:<id> bridge in _resolve.

Task 1 of pre-slice2-hardening: a RunSpec with task_id="suite:<suite_id>"
resolves to a SuiteRunner instance via the shared adapter gate, without touching
pack.tasks() at all.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import clousight_bench.core.orchestrator as orch
from clousight_bench.core.errors import UnknownTaskError
from clousight_bench.core.schema import RunSpec
from clousight_bench.core.suite_runner import SuiteRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(task_id: str = "suite:swe-bench", **kwargs) -> RunSpec:
    return RunSpec(
        domain="agent-runtime",
        task_id=task_id,
        platform="local-sim",
        target={"mode": "mock"},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test 1: basic bridge — resolves to SuiteRunner
# ---------------------------------------------------------------------------


def test_bridge_resolves_suite_task():
    """_resolve returns (pack, SuiteRunner, adapter_cls) for suite:<id> specs."""
    spec = _spec()
    pack, task, adapter_cls = orch._resolve(spec)
    assert isinstance(task, SuiteRunner), f"expected SuiteRunner, got {type(task)}"
    assert task.task_id == "suite:swe-bench"
    assert task.mock is True
    assert task._params == {}  # white-box: the bridge must store spec.params verbatim


# ---------------------------------------------------------------------------
# Test 2: unknown suite → UnknownTaskError mentioning known suites
# ---------------------------------------------------------------------------


def test_bridge_unknown_suite_raises():
    """A suite:<id> for an unregistered id raises UnknownTaskError."""
    spec = _spec(task_id="suite:nope")
    with pytest.raises(UnknownTaskError) as exc_info:
        orch._resolve(spec)
    # The error should mention the known suite so the user can pick one.
    assert "swe-bench" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: explicit evaluator override via params
# ---------------------------------------------------------------------------


def test_bridge_explicit_evaluator_valid():
    """params={"evaluator": "official-swe-evaluator"} resolves successfully."""
    spec = _spec(params={"evaluator": "official-swe-evaluator"})
    _, task, _ = orch._resolve(spec)
    assert isinstance(task, SuiteRunner)
    assert task.task_id == "suite:swe-bench"
    # The wanted-evaluator filter must actually SELECT it, not merely tolerate the key.
    assert task._evaluator.evaluator_id == "official-swe-evaluator"
    assert task._params.get("evaluator") == "official-swe-evaluator"


def test_bridge_explicit_evaluator_invalid():
    """params={"evaluator": "no-such"} raises UnknownTaskError."""
    spec = _spec(params={"evaluator": "no-such"})
    with pytest.raises(UnknownTaskError) as exc_info:
        orch._resolve(spec)
    assert "no-such" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 4: end-to-end mock run completes with swe-bench measurements
# ---------------------------------------------------------------------------


def test_bridge_end_to_end_mock_run(tmp_path, monkeypatch):
    """orchestrator.execute with suite:swe-bench completes and has swe-bench.resolved."""
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="agent-runtime",
        task_id="suite:swe-bench",
        platform="local-sim",
        target={"mode": "mock"},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"expected completed, got {record.status}: {record.errors}"
    assert "swe-bench.resolved" in record.measurements, (
        f"swe-bench.resolved not in measurements: {list(record.measurements)}"
    )
    assert record.provenance.suite_id == "swe-bench", f"provenance.suite_id={record.provenance.suite_id!r}"


# ---------------------------------------------------------------------------
# Test 5: target mode: live → mock is False (do NOT execute)
# ---------------------------------------------------------------------------


def test_bridge_nonmock_flag():
    """target.mode='live' constructs the task with mock=False (not executed here)."""
    spec = RunSpec(
        domain="agent-runtime",
        task_id="suite:swe-bench",
        platform="local-sim",
        target={"mode": "live"},
    )
    # Only the constructed task is checked — a real non-mock execute is slice-2 work.
    _, task, _ = orch._resolve(spec)
    assert isinstance(task, SuiteRunner)
    assert task.mock is False


# ---------------------------------------------------------------------------
# Test 6: _resolve now accepts results_dir; direct callers supply a tmp_path
# ---------------------------------------------------------------------------


def test_bridge_resolve_accepts_results_dir(tmp_path):
    """_resolve(spec, results_dir) passes artifacts_root into the SuiteRunner."""
    spec = _spec()
    pack, task, adapter_cls = orch._resolve(spec, tmp_path)
    assert isinstance(task, SuiteRunner)
    # The artifacts_root inside the task must be results_dir/artifacts
    assert task._artifacts_root == tmp_path / "artifacts"


# ---------------------------------------------------------------------------
# Test 7: end-to-end — persisted record JSON has no absolute tmp paths
# ---------------------------------------------------------------------------


def test_end_to_end_record_has_no_tmp_leak(tmp_path, monkeypatch):
    """Full orchestrator.execute mock run: persisted record JSON must contain no
    absolute temp-dir paths; artifacts must live under results_dir/artifacts/."""
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    spec = RunSpec(
        domain="agent-runtime",
        task_id="suite:swe-bench",
        platform="local-sim",
        target={"mode": "mock"},
    )
    record = orch.execute(spec, results_dir=tmp_path, enrich=False, preflight=False)
    assert record.status == "completed", f"expected completed: {record.errors}"

    # Locate the persisted record file (stored under domain/adapter/ subdir)
    record_files = [p for p in tmp_path.rglob("*.json") if p.name != ".cost_ledger.json"]
    assert record_files, "no record JSON found under results_dir"
    record_text = record_files[0].read_text()

    # No absolute temp paths must appear in the persisted record
    tmp_gettempdir = tempfile.gettempdir()
    tmp_resolved = str(Path(tmp_gettempdir).resolve())
    assert tmp_gettempdir not in record_text, f"record contains tempfile.gettempdir() path {tmp_gettempdir!r}"
    assert tmp_resolved not in record_text, f"record contains resolved tmpdir {tmp_resolved!r}"

    # Artifacts must live under results_dir/artifacts/
    artifacts_dir = tmp_path / "artifacts"
    assert artifacts_dir.is_dir(), "artifacts/ subdir was not created under results_dir"
    staged = list(artifacts_dir.iterdir())
    assert staged, "no subdir found inside artifacts/"
