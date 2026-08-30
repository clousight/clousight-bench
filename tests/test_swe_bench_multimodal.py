"""Tests for the SWE-bench Multimodal suite + evaluator (thin variant).

Covers the Multimodal subclass identity/dataset binding, its bundled REAL
image-augmented dev-split fixtures, the ``swe-bench-multimodal.`` namespace, AND
regression guards that the additive parametrization refactor left the flagship
Verified suite unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator
from clousight_bench.suites.swe_bench.suite import SweBenchSuite
from clousight_bench.suites.swe_bench_multimodal.evaluator import (
    OfficialSweMultimodalEvaluator,
)
from clousight_bench.suites.swe_bench_multimodal.suite import (
    _HF_REVISION,
    SweBenchMultimodalSuite,
)

_MM_FIXTURES = Path(SweBenchMultimodalSuite.fixtures_dir)


# ---------------------------------------------------------------------------
# identity / dataset binding
# ---------------------------------------------------------------------------


def test_mm_is_a_swebench_subclass() -> None:
    assert issubclass(SweBenchMultimodalSuite, SweBenchSuite)
    assert issubclass(OfficialSweMultimodalEvaluator, OfficialSweEvaluator)


def test_mm_identity_attrs() -> None:
    assert SweBenchMultimodalSuite.suite_id == "swe-bench-multimodal"
    assert SweBenchMultimodalSuite.dataset_name == "princeton-nlp/SWE-bench_Multimodal"
    # dev split, deliberately: the test-split gold patches are held out (hosted sb-cli).
    assert SweBenchMultimodalSuite.split == "dev"
    assert SweBenchMultimodalSuite.suite_version == _HF_REVISION


def test_mm_hf_revision_is_real_pin() -> None:
    assert _HF_REVISION == "princeton-nlp/SWE-bench_Multimodal@aa2db68940196b6b59ae3f577faa0c25157bdd50"
    assert "@aa2db689" in _HF_REVISION
    assert "abc1234" not in _HF_REVISION


def test_mm_fixtures_dir_is_specific() -> None:
    assert SweBenchMultimodalSuite.fixtures_dir != SweBenchSuite.fixtures_dir
    assert "swe_bench_multimodal" in str(SweBenchMultimodalSuite.fixtures_dir)


# ---------------------------------------------------------------------------
# real, genuinely-multimodal bundled fixtures
# ---------------------------------------------------------------------------


def test_mm_instances_full_matches_subset_and_carries_images() -> None:
    """Rows carry the 6 base fields + a non-empty ``image_assets`` (the whole point)."""
    full = json.loads((_MM_FIXTURES / "instances_full.json").read_text())
    subset = json.loads((_MM_FIXTURES / "instances_subset.json").read_text())

    full_by_id = {row["instance_id"]: row for row in full}
    assert set(full_by_id) == {r["instance_id"] for r in subset}

    required = {"instance_id", "repo", "base_commit", "problem_statement", "hints_text", "patch"}
    for row in full:
        # 7 fields: the 6 base SWE-bench fields + image_assets
        assert set(row.keys()) == required | {"image_assets"}, f"{row['instance_id']}: keys {sorted(row)}"
        for field in sorted(required - {"hints_text"}):
            assert row[field], f"{row['instance_id']}: field {field!r} is empty"
        assert row["patch"].startswith(("diff --git", "---")), f"{row['instance_id']}: not a diff"
        # image_assets must be real image URLs — a Multimodal suite that stripped
        # the images would silently degrade to plain SWE-bench on JS repos.
        assets = json.loads(row["image_assets"])
        urls = assets.get("problem_statement", [])
        assert urls, f"{row['instance_id']}: no problem_statement images"
        assert all(u.startswith("http") for u in urls), f"{row['instance_id']}: non-URL image asset"

    for r in subset:
        assert r["patch"] == full_by_id[r["instance_id"]]["patch"]


def test_mm_fixtures_are_js_repos_distinct_from_verified() -> None:
    mm = json.loads((_MM_FIXTURES / "instances_full.json").read_text())
    mm_ids = {r["instance_id"] for r in mm}
    verified = {
        r["instance_id"] for r in json.loads((SweBenchSuite.fixtures_dir / "instances_full.json").read_text())
    }
    assert mm_ids.isdisjoint(verified)


def test_mm_load_instance_returns_row_with_images() -> None:
    row = SweBenchMultimodalSuite()._load_instance("chartjs__Chart.js-10301")
    assert row["repo"] == "chartjs/Chart.js"
    assert row["patch"].startswith("diff --git")
    assert "image_assets" in row
    assert json.loads(row["image_assets"])["problem_statement"]


def test_mm_load_instance_cache_isolated_from_base() -> None:
    SweBenchSuite()._load_instance("django__django-11099")  # Verified id
    SweBenchMultimodalSuite()._load_instance("chartjs__Chart.js-10301")  # MM id
    with pytest.raises(KeyError):
        SweBenchMultimodalSuite()._load_instance("django__django-11099")
    with pytest.raises(KeyError):
        SweBenchSuite()._load_instance("chartjs__Chart.js-10301")


# ---------------------------------------------------------------------------
# mock_artifacts + resolve (inherited logic, MM fixtures)
# ---------------------------------------------------------------------------


def test_mm_mock_artifacts_uses_mm_fixtures(tmp_path: Path) -> None:
    ra = SweBenchMultimodalSuite().mock_artifacts({"_tmp_dir": str(tmp_path)})
    assert set(ra.manifest) == {"predictions", "results", "trajectory", "usage"}
    for key in ra.manifest:
        assert ra.path(key).exists()
    results = json.loads(ra.path("results").read_text())
    assert results["total"] == 3
    assert results["resolved"] == 2


def test_mm_resolve_reads_mm_subset() -> None:
    dh = SweBenchMultimodalSuite().resolve({}, assets=None)
    assert dh.version == _HF_REVISION
    ids = dh.payload["instance_ids"]
    assert "chartjs__Chart.js-10301" in ids
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# evaluator namespace + supports()
# ---------------------------------------------------------------------------


def test_mm_evaluator_emits_mm_namespace(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text(
        json.dumps({"per_instance": {"a": {"resolved": True}}, "resolved": 1, "total": 2})
    )
    raw = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "results.json"}})
    out = OfficialSweMultimodalEvaluator().evaluate(raw)
    assert set(out) == {"swe-bench-multimodal.resolved"}
    assert out["swe-bench-multimodal.resolved"].value == 0.5
    assert out["swe-bench-multimodal.resolved"].official is True


def test_mm_evaluator_supports_only_mm() -> None:
    ev = OfficialSweMultimodalEvaluator()
    assert ev.supports("swe-bench-multimodal", "any")
    assert not ev.supports("swe-bench", "any")
    assert not ev.supports("swe-bench-lite", "any")


# ---------------------------------------------------------------------------
# REGRESSION: flagship Verified suite intact
# ---------------------------------------------------------------------------


def test_flagship_verified_identity_unchanged() -> None:
    assert SweBenchSuite.suite_id == "swe-bench"
    assert SweBenchSuite.dataset_name == "princeton-nlp/SWE-bench_Verified"
    assert SweBenchSuite.split == "test"


def test_flagship_evaluator_still_emits_swe_bench_namespace(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text(
        json.dumps({"per_instance": {"a": {"resolved": True}}, "resolved": 1, "total": 2})
    )
    raw = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "results.json"}})
    ev = OfficialSweEvaluator()
    out = ev.evaluate(raw)
    assert set(out) == {"swe-bench.resolved"}
    assert ev.supports("swe-bench", "any")
    assert not ev.supports("swe-bench-multimodal", "any")
