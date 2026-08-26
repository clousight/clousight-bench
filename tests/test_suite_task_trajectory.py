"""Tests for SuiteTask trajectory artifact surfacing.

When a BenchmarkSuite's mock_artifacts() manifest contains a 'trajectory' key,
SuiteTask.execute() must surface a trajectory artifact in the ObservationBundle
that passes validate_observation_bundle (kind/media/sha256/path all present).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from clousight_bench.core.observation import Measurement, validate_observation_bundle
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    EnvHandle,
    Evaluator,
    RawArtifacts,
)
from clousight_bench.core.suite_task import SuiteTask

# ---------------------------------------------------------------------------
# Minimal stub suite that emits a trajectory manifest entry
# ---------------------------------------------------------------------------


class _SuiteWithTrajectory(BenchmarkSuite):
    suite_id = "traj-demo"
    suite_version = "v1"

    def resolve(self, cfg, assets):
        return DatasetHandle("v1", "sha256:d", {})

    def prepare(self, target, dataset, driver):
        return EnvHandle({})

    def run(self, target, env, driver):
        raise AssertionError("mock path should not call run")

    def mock_artifacts(self, cfg):
        d = Path(cfg["_tmp"])
        # Write result file
        results_path = d / "r.json"
        results_path.write_text('{"resolved": 1, "total": 2}')

        # Write trajectory file
        traj_path = d / "trajectory.jsonl"
        traj_content = (
            '{"span_id":"s1","trace_id":"trace-t1","parent_id":null,"name":"run","kind":"llm_call",'
            '"t_start":1.0,"t_end":2.0,"status":"ok","attrs":{}}\n'
            '{"span_id":"s2","trace_id":"trace-t1","parent_id":"s1","name":"tool","kind":"tool_call",'
            '"t_start":2.0,"t_end":2.1,"status":"ok","attrs":{}}\n'
        )
        traj_path.write_text(traj_content)
        traj_sha = "sha256:" + hashlib.sha256(traj_path.read_bytes()).hexdigest()

        return RawArtifacts(
            d,
            {
                "results": {"path": "r.json", "sha256": "sha256:x", "rows": None},
                "trajectory": {
                    "path": "trajectory.jsonl",
                    "sha256": traj_sha,
                    "rows": 2,
                },
            },
        )


class _Eval(Evaluator):
    evaluator_id = "traj-eval"
    official = True

    def supports(self, suite_id, product):
        return suite_id == "traj-demo"

    def evaluate(self, raw):
        import json

        r = json.loads(raw.path("results").read_text())
        return {
            "traj.resolved": Measurement(
                r["resolved"] / r["total"],
                "ratio",
                reproducibility_class="deterministic",
                official=True,
            )
        }


# ---------------------------------------------------------------------------
# Minimal stub suite WITHOUT a trajectory entry (regression guard)
# ---------------------------------------------------------------------------


class _SuiteWithoutTrajectory(BenchmarkSuite):
    suite_id = "no-traj"
    suite_version = "v1"

    def resolve(self, cfg, assets):
        return DatasetHandle("v1", "sha256:d", {})

    def prepare(self, target, dataset, driver):
        return EnvHandle({})

    def run(self, target, env, driver):
        raise AssertionError("mock path should not call run")

    def mock_artifacts(self, cfg):
        d = Path(cfg["_tmp"])
        (d / "r.json").write_text('{"resolved": 1, "total": 2}')
        return RawArtifacts(d, {"results": {"path": "r.json", "sha256": "sha256:x", "rows": None}})


class _EvalNoTraj(Evaluator):
    evaluator_id = "no-traj-eval"
    official = True

    def supports(self, suite_id, product):
        return suite_id == "no-traj"

    def evaluate(self, raw):
        return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_suite_task_surfaces_trajectory_artifact(tmp_path) -> None:
    """SuiteTask.execute() adds a trajectory artifact when manifest has 'trajectory'."""
    st = SuiteTask(_SuiteWithTrajectory(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})

    traj_artifacts = [a for a in bundle.artifacts if a.get("kind") == "trajectory"]
    assert len(traj_artifacts) == 1, f"Expected 1 trajectory artifact, got {bundle.artifacts!r}"


def test_suite_task_trajectory_artifact_passes_validate_observation_bundle(tmp_path) -> None:
    """The trajectory artifact must satisfy validate_observation_bundle requirements."""
    st = SuiteTask(_SuiteWithTrajectory(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})
    # Must not raise
    validate_observation_bundle(bundle)


def test_suite_task_trajectory_artifact_has_correct_shape(tmp_path) -> None:
    """The trajectory artifact has kind, media, sha256, and path."""
    st = SuiteTask(_SuiteWithTrajectory(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})

    traj = next(a for a in bundle.artifacts if a.get("kind") == "trajectory")
    assert traj["kind"] == "trajectory"
    assert traj["media"] == "application/jsonl"
    assert traj["sha256"].startswith("sha256:")
    assert "path" in traj
    assert traj["path"].endswith("trajectory.jsonl")


def test_suite_task_no_trajectory_artifact_when_manifest_lacks_trajectory(tmp_path) -> None:
    """When manifest has no 'trajectory' key, artifacts list is empty."""
    st = SuiteTask(_SuiteWithoutTrajectory(), _EvalNoTraj(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})

    traj_artifacts = [a for a in bundle.artifacts if a.get("kind") == "trajectory"]
    assert traj_artifacts == []


def test_suite_task_trajectory_artifact_sha256_matches_file(tmp_path) -> None:
    """The sha256 in the artifact matches the actual file content."""
    artifacts_root = tmp_path / "arts"
    artifacts_root.mkdir()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    st = SuiteTask(_SuiteWithTrajectory(), _Eval(), mock=True, artifacts_root=artifacts_root)
    bundle = st.execute(adapter=None, params={"_tmp": str(raw_dir)})

    traj = next(a for a in bundle.artifacts if a.get("kind") == "trajectory")
    # path is relative to artifacts_root; resolve it for reading
    file_path = artifacts_root / traj["path"]
    actual_sha = "sha256:" + hashlib.sha256(file_path.read_bytes()).hexdigest()
    assert traj["sha256"] == actual_sha


def test_suite_task_swe_bench_mock_surfaces_trajectory(tmp_path) -> None:
    """The real SweBenchSuite.mock_artifacts() also produces a trajectory artifact."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    st = SuiteTask(SweBenchSuite(), OfficialSweEvaluator(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp_dir": str(tmp_path)})

    traj_artifacts = [a for a in bundle.artifacts if a.get("kind") == "trajectory"]
    assert len(traj_artifacts) == 1
    validate_observation_bundle(bundle)


# ---------------------------------------------------------------------------
# Schema v2 validation — execute raises on invalid span in trajectory
# ---------------------------------------------------------------------------


class _SuiteWithBadTrajectory(BenchmarkSuite):
    """Suite whose trajectory.jsonl has an invalid span on line 2."""

    suite_id = "bad-traj"
    suite_version = "v1"

    def resolve(self, cfg, assets):
        return DatasetHandle("v1", "sha256:d", {})

    def prepare(self, target, dataset, driver):
        return EnvHandle({})

    def run(self, target, env, driver):
        raise AssertionError("mock path should not call run")

    def mock_artifacts(self, cfg):
        import hashlib

        d = Path(cfg["_tmp"])
        (d / "r.json").write_text('{"resolved": 1, "total": 2}')

        traj_path = d / "trajectory.jsonl"
        # Line 1: valid v2 span; Line 2: missing trace_id / status (invalid)
        traj_content = (
            '{"span_id":"s1","trace_id":"t1","parent_id":null,"name":"run","kind":"llm_call",'
            '"t_start":1.0,"t_end":2.0,"status":"ok","attrs":{}}\n'
            '{"span_id":"s2","parent_id":"s1","name":"tool","kind":"tool_call",'
            '"t_start":2.0,"t_end":2.1,"attrs":{}}\n'  # missing trace_id, status
        )
        traj_path.write_text(traj_content)
        traj_sha = "sha256:" + hashlib.sha256(traj_path.read_bytes()).hexdigest()

        return RawArtifacts(
            d,
            {
                "results": {"path": "r.json", "sha256": "sha256:x", "rows": None},
                "trajectory": {
                    "path": "trajectory.jsonl",
                    "sha256": traj_sha,
                    "rows": 2,
                },
            },
        )


class _EvalBadTraj(Evaluator):
    evaluator_id = "bad-traj-eval"
    official = True

    def supports(self, suite_id, product):
        return suite_id == "bad-traj"

    def evaluate(self, raw):
        return {}


def test_suite_task_execute_raises_on_invalid_span_in_trajectory(tmp_path) -> None:
    """execute() raises ValueError matching 'trajectory.jsonl:2' for invalid span on line 2."""
    st = SuiteTask(_SuiteWithBadTrajectory(), _EvalBadTraj(), mock=True)
    import pytest

    with pytest.raises(ValueError, match="trajectory.jsonl:2"):
        st.execute(adapter=None, params={"_tmp": str(tmp_path)})
