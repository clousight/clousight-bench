"""`csbench run --assert <thresholds>` — the non-pytest CI gate (exit code)."""

from __future__ import annotations

import json

from clousight_bench.cli import main


def _mock_cfg(tmp_path):
    cfg = tmp_path / "mock.yaml"
    cfg.write_text("target:\n  mode: mock\nparams: {}\n")
    return str(cfg)


def _run(tmp_path, thresholds: dict) -> int:
    thr = tmp_path / "thr.json"
    thr.write_text(json.dumps({"thresholds": thresholds}))
    return main(
        [
            "run",
            "--domain",
            "llm",
            "--task",
            "suite:mmlu",
            "--platform",
            "llm-mock",
            "--config",
            _mock_cfg(tmp_path),
            "--results",
            str(tmp_path / "r"),
            "--no-enrich",
            "--assert",
            str(thr),
        ]
    )


def test_exit_zero_when_thresholds_met(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    assert _run(tmp_path, {"mmlu.accuracy": {"min": 0.9}}) == 0


def test_exit_nonzero_when_threshold_unmet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    assert _run(tmp_path, {"mmlu.accuracy": {"min": 1.5}}) != 0


def test_exit_nonzero_when_gated_measurement_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    assert _run(tmp_path, {"nope.metric": {"min": 1}}) != 0
