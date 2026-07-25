"""GSM8K end-to-end asset demo.

Offline: the workload manifest + asset spec stay valid (no network).
Online (opt-in): actually download -> checksum-verify -> cache -> consume,
gated behind CLOUSIGHT_BENCH_NETWORK_TESTS=1 so CI stays fast and offline.
"""
import os
from pathlib import Path

import pytest
import yaml

from clousight_bench.core.assets import REMOTE, load_asset_specs
from clousight_bench.core.resources import reference_workload_path
from clousight_bench.core.workload import WorkloadEngine

_WL = reference_workload_path("gsm8k-stats")
_NETWORK = os.environ.get("CLOUSIGHT_BENCH_NETWORK_TESTS") == "1"


def test_manifest_and_asset_spec_valid_offline():
    manifest = yaml.safe_load((_WL / "manifest.yaml").read_text(encoding="utf-8"))
    specs = load_asset_specs(manifest)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "gsm8k-test" and spec.source == REMOTE
    assert spec.uri.endswith("test.jsonl")
    assert len(spec.sha256) == 64  # pinned digest for reproducibility
    assert spec.license  # remote must be auditable


@pytest.mark.skipif(not _NETWORK, reason="set CLOUSIGHT_BENCH_NETWORK_TESTS=1 to run")
def test_gsm8k_download_and_run(tmp_path):
    eng = WorkloadEngine(_WL)
    resolved = eng.resolve_assets(cache_dir=tmp_path)
    assert Path(resolved["gsm8k-test"]).exists()  # downloaded + checksum-verified

    result = eng.run()
    assert result.ok
    assert result.metrics["num_problems"] == 1319
    assert result.metrics["final_answer_rate"] == 1.0
    assert result.metrics["avg_reasoning_steps"] > 0
