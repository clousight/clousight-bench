"""Tests for the SWE-bench Lite suite + evaluator (thin variant of SWE-bench).

Covers the Lite subclass identity/dataset binding, its bundled REAL fixtures,
the ``swe-bench-lite.`` measurement namespace, AND regression guards that the
additive parametrization refactor left the flagship Verified suite unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clousight_bench.core.suite import RawArtifacts
from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator
from clousight_bench.suites.swe_bench.suite import SweBenchSuite
from clousight_bench.suites.swe_bench_lite.evaluator import OfficialSweLiteEvaluator
from clousight_bench.suites.swe_bench_lite.suite import (
    _HF_REVISION,
    SweBenchLiteSuite,
)

_LITE_FIXTURES = Path(SweBenchLiteSuite.fixtures_dir)


# ---------------------------------------------------------------------------
# identity / dataset binding
# ---------------------------------------------------------------------------


def test_lite_is_a_swebench_subclass() -> None:
    """Lite reuses the flagship harness via inheritance, not copy-paste."""
    assert issubclass(SweBenchLiteSuite, SweBenchSuite)
    assert issubclass(OfficialSweLiteEvaluator, OfficialSweEvaluator)


def test_lite_identity_attrs() -> None:
    assert SweBenchLiteSuite.suite_id == "swe-bench-lite"
    assert SweBenchLiteSuite.dataset_name == "princeton-nlp/SWE-bench_Lite"
    assert SweBenchLiteSuite.split == "test"
    assert SweBenchLiteSuite.suite_version == _HF_REVISION


def test_lite_hf_revision_is_real_pin() -> None:
    """The pin is the REAL Lite main-commit, not a placeholder."""
    assert _HF_REVISION == "princeton-nlp/SWE-bench_Lite@6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"
    assert "@6ec7bb89" in _HF_REVISION
    assert "abc1234" not in _HF_REVISION


def test_lite_fixtures_dir_is_lite_specific() -> None:
    """Lite must point at its OWN fixtures dir, not the Verified one."""
    assert SweBenchLiteSuite.fixtures_dir != SweBenchSuite.fixtures_dir
    assert SweBenchLiteSuite.fixtures_dir.name == "fixtures"
    assert "swe_bench_lite" in str(SweBenchLiteSuite.fixtures_dir)


# ---------------------------------------------------------------------------
# real bundled fixtures
# ---------------------------------------------------------------------------


def test_lite_instances_full_matches_subset() -> None:
    """instances_full.json carries a real 6-field row for every bundled subset id."""
    full = json.loads((_LITE_FIXTURES / "instances_full.json").read_text())
    subset = json.loads((_LITE_FIXTURES / "instances_subset.json").read_text())

    full_by_id = {row["instance_id"]: row for row in full}
    assert set(full_by_id) == {r["instance_id"] for r in subset}

    required = {"instance_id", "repo", "base_commit", "problem_statement", "hints_text", "patch"}
    for row in full:
        assert set(row.keys()) == required, f"{row['instance_id']}: keys {sorted(row)}"
        for field in sorted(required - {"hints_text"}):
            assert row[field], f"{row['instance_id']}: field {field!r} is empty"
        assert isinstance(row["hints_text"], str)
        assert row["patch"].startswith(("diff --git", "---")), (
            f"{row['instance_id']}: patch does not look like a unified diff"
        )

    for r in subset:
        assert r["patch"] == full_by_id[r["instance_id"]]["patch"], (
            f"{r['instance_id']}: subset patch drifted from instances_full.json"
        )


def test_lite_fixtures_are_distinct_from_verified() -> None:
    """The Lite instance ids must not be the Verified ids (real different split)."""
    lite = {r["instance_id"] for r in json.loads((_LITE_FIXTURES / "instances_full.json").read_text())}
    verified = {
        r["instance_id"] for r in json.loads((SweBenchSuite.fixtures_dir / "instances_full.json").read_text())
    }
    assert lite.isdisjoint(verified)


def test_lite_load_instance_returns_real_row() -> None:
    row = SweBenchLiteSuite()._load_instance("astropy__astropy-12907")
    assert row["repo"] == "astropy/astropy"
    assert row["patch"].startswith("diff --git")
    assert len(row["problem_statement"]) > 0


def test_lite_load_instance_cache_is_isolated_from_base() -> None:
    """Lite's instance cache must not be shadowed by / pollute the base cache."""
    # Populate both caches, then assert each returns its OWN split's rows.
    SweBenchSuite()._load_instance("django__django-11099")  # Verified id
    SweBenchLiteSuite()._load_instance("astropy__astropy-12907")  # Lite id
    with pytest.raises(KeyError):
        SweBenchLiteSuite()._load_instance("django__django-11099")  # Verified-only id
    with pytest.raises(KeyError):
        SweBenchSuite()._load_instance("astropy__astropy-12907")  # Lite-only id


# ---------------------------------------------------------------------------
# mock_artifacts + resolve (inherited logic, Lite fixtures)
# ---------------------------------------------------------------------------


def test_lite_mock_artifacts_uses_lite_fixtures(tmp_path: Path) -> None:
    ra = SweBenchLiteSuite().mock_artifacts({"_tmp_dir": str(tmp_path)})
    assert set(ra.manifest) == {"predictions", "results", "trajectory", "usage"}
    for key in ra.manifest:
        assert ra.path(key).exists()
    results = json.loads(ra.path("results").read_text())
    assert results["total"] == 3
    assert results["resolved"] == 2


def test_lite_resolve_reads_lite_subset() -> None:
    dh = SweBenchLiteSuite().resolve({}, assets=None)
    assert dh.version == _HF_REVISION
    ids = dh.payload["instance_ids"]
    assert "astropy__astropy-12907" in ids
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# evaluator namespace + supports()
# ---------------------------------------------------------------------------


def test_lite_evaluator_emits_lite_namespace(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text(
        json.dumps({"per_instance": {"a": {"resolved": True}}, "resolved": 1, "total": 2})
    )
    raw = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "results.json"}})
    out = OfficialSweLiteEvaluator().evaluate(raw)
    assert set(out) == {"swe-bench-lite.resolved"}
    assert out["swe-bench-lite.resolved"].value == 0.5
    assert out["swe-bench-lite.resolved"].official is True


def test_lite_evaluator_supports_only_lite() -> None:
    ev = OfficialSweLiteEvaluator()
    assert ev.supports("swe-bench-lite", "any")
    assert not ev.supports("swe-bench", "any")


# ---------------------------------------------------------------------------
# REGRESSION: the additive refactor left the flagship Verified suite intact
# ---------------------------------------------------------------------------


def test_flagship_verified_identity_unchanged() -> None:
    assert SweBenchSuite.suite_id == "swe-bench"
    assert SweBenchSuite.dataset_name == "princeton-nlp/SWE-bench_Verified"
    assert "SWE-bench_Verified" in SweBenchSuite.suite_version


def test_flagship_evaluator_still_emits_swe_bench_namespace(tmp_path: Path) -> None:
    (tmp_path / "results.json").write_text(
        json.dumps({"per_instance": {"a": {"resolved": True}}, "resolved": 1, "total": 2})
    )
    raw = RawArtifacts(dir=tmp_path, manifest={"results": {"path": "results.json"}})
    ev = OfficialSweEvaluator()
    out = ev.evaluate(raw)
    assert set(out) == {"swe-bench.resolved"}
    assert ev.supports("swe-bench", "any")
    assert not ev.supports("swe-bench-lite", "any")
