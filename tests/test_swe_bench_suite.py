"""Tests for the SWE-bench Verified suite plugin + mock agent.

TDD-first: these tests are written before the implementation.
Real run() / docker path is NOT tested here (Task 6).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return f"sha256:{h}"


# ---------------------------------------------------------------------------
# mock_artifacts
# ---------------------------------------------------------------------------


def test_mock_artifacts_returns_all_four_keys(tmp_path):
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    ra = suite.mock_artifacts({"_tmp_dir": str(tmp_path)})
    assert set(ra.manifest.keys()) == {"predictions", "results", "trajectory", "usage"}


def test_mock_artifacts_files_exist_on_disk(tmp_path):
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    ra = suite.mock_artifacts({"_tmp_dir": str(tmp_path)})
    for key in ra.manifest:
        assert ra.path(key).exists(), f"artifact {key!r} file is missing"


def test_mock_artifacts_sha256_hashes_match(tmp_path):
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    ra = suite.mock_artifacts({"_tmp_dir": str(tmp_path)})
    for key, meta in ra.manifest.items():
        actual = _sha256_file(ra.path(key))
        assert actual == meta["sha256"], (
            f"sha256 mismatch for {key!r}: manifest says {meta['sha256']!r} but file is {actual!r}"
        )


def test_mock_artifacts_manifest_rows_field_present(tmp_path):
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    ra = suite.mock_artifacts({"_tmp_dir": str(tmp_path)})
    for key, meta in ra.manifest.items():
        assert "rows" in meta, f"manifest entry {key!r} is missing 'rows'"


def test_mock_artifacts_uses_tempdir_when_no_tmp_dir_provided():
    """mock_artifacts must work even without _tmp_dir in cfg."""
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    ra = suite.mock_artifacts({})
    assert set(ra.manifest.keys()) == {"predictions", "results", "trajectory", "usage"}
    for key in ra.manifest:
        assert ra.path(key).exists()


# ---------------------------------------------------------------------------
# resolve — determinism + sensitivity
# ---------------------------------------------------------------------------


def test_resolve_digest_is_deterministic():
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    cfg = {"instance_ids": ["django__django-11099", "sympy__sympy-20590"]}
    d1 = suite.resolve(cfg, None)
    d2 = suite.resolve(cfg, None)
    assert d1.digest == d2.digest


def test_resolve_digest_changes_when_instance_set_changes():
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    d1 = suite.resolve({"instance_ids": ["django__django-11099"]}, None)
    d2 = suite.resolve({"instance_ids": ["sympy__sympy-20590"]}, None)
    assert d1.digest != d2.digest


def test_resolve_returns_dataset_handle_with_correct_fields():
    from clousight_bench.core.suite import DatasetHandle
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    cfg = {"instance_ids": ["django__django-11099"]}
    dh = suite.resolve(cfg, None)
    assert isinstance(dh, DatasetHandle)
    assert dh.version == suite.suite_version
    assert dh.digest.startswith("sha256:")
    assert dh.payload["instance_ids"] == ["django__django-11099"]


def test_resolve_reads_fixture_when_no_instance_ids_in_cfg():
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    dh = suite.resolve({}, None)
    # Should load at least the 2 fixture instances
    assert len(dh.payload["instance_ids"]) >= 2


def test_resolve_digest_is_order_independent():
    """digest must be based on sorted(instance_ids), not insertion order."""
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    d1 = suite.resolve({"instance_ids": ["django__django-11099", "sympy__sympy-20590"]}, None)
    d2 = suite.resolve({"instance_ids": ["sympy__sympy-20590", "django__django-11099"]}, None)
    assert d1.digest == d2.digest


# ---------------------------------------------------------------------------
# prepare + teardown
# ---------------------------------------------------------------------------


def test_prepare_returns_env_handle():
    """prepare() must return an EnvHandle with a materialised payload (not empty)."""
    from clousight_bench.core.suite import DatasetHandle, DriverContext, EnvHandle, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=True)
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": ["django__django-11099"], "agent_kind": "gold"},
    )
    driver = DriverContext(placement="local")
    env = suite.prepare(target, dataset, driver)
    assert isinstance(env, EnvHandle)
    # The payload must be non-empty — prepare materialises the env
    assert env.payload, "prepare() must return a non-empty payload"


def test_teardown_is_noop():
    from clousight_bench.core.suite import EnvHandle
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    result = suite.teardown(EnvHandle({}))
    assert result is None


# ---------------------------------------------------------------------------
# MockAgent
# ---------------------------------------------------------------------------


def test_mock_agent_gold_returns_nonempty_string():
    from clousight_bench.suites.swe_bench.mock_agent import MockAgent

    agent = MockAgent()
    patch = agent.patch_for("django__django-11099", "gold")
    assert isinstance(patch, str) and len(patch) > 0


def test_mock_agent_empty_returns_empty_string():
    from clousight_bench.suites.swe_bench.mock_agent import MockAgent

    agent = MockAgent()
    patch = agent.patch_for("django__django-11099", "empty")
    assert patch == ""


def test_mock_agent_empty_returns_empty_for_any_instance():
    from clousight_bench.suites.swe_bench.mock_agent import MockAgent

    agent = MockAgent()
    for iid in ["django__django-11099", "sympy__sympy-20590", "nonexistent__repo-99999"]:
        assert agent.patch_for(iid, "empty") == ""


# ---------------------------------------------------------------------------
# suite_id / suite_version class attributes
# ---------------------------------------------------------------------------


def test_suite_id_class_attribute():
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    assert SweBenchSuite.suite_id == "swe-bench"


def test_suite_version_class_attribute():
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    assert "SWE-bench_Verified" in SweBenchSuite.suite_version
    assert len(SweBenchSuite.suite_version) > 10


# ---------------------------------------------------------------------------
# B-slice-2 Task 1: real HF pin + real gold-patch fixtures
# ---------------------------------------------------------------------------


def test_hf_revision_is_real_pin() -> None:
    """_HF_REVISION is the REAL Verified main-commit pin, not the placeholder."""
    from clousight_bench.suites.swe_bench.suite import _HF_REVISION

    assert _HF_REVISION == ("princeton-nlp/SWE-bench_Verified@c104f840cc67f8b6eec6f759ebc8b2693d585d4a")
    assert "@c104f840" in _HF_REVISION
    assert "abc1234" not in _HF_REVISION


def test_instances_full_matches_subset() -> None:
    """instances_full.json carries a real 6-field row for every bundled subset id.

    hints_text may legitimately be empty (django__django-11099 has no hints in the
    real dataset); all other fields must be non-empty.  Subset gold patches must be
    byte-identical to the full rows' patches so the two fixtures cannot drift.
    """
    import json

    full = json.loads((_FIXTURES_DIR / "instances_full.json").read_text())
    subset = json.loads((_FIXTURES_DIR / "instances_subset.json").read_text())

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


def test_load_instance_returns_full_row() -> None:
    """_load_instance returns the real dataset row for a bundled id."""
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    row = SweBenchSuite()._load_instance("sympy__sympy-20590")
    assert row["repo"] == "sympy/sympy"
    assert row["patch"].startswith("diff --git")
    assert len(row["problem_statement"]) > 0


def test_load_instance_unknown_raises() -> None:
    """_load_instance raises KeyError listing the sorted available ids on a miss."""
    import pytest

    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    with pytest.raises(KeyError, match="django__django-11099"):
        SweBenchSuite()._load_instance("nonexistent__repo-99999")


# ---------------------------------------------------------------------------
# run() — real docker path raises without extra
# ---------------------------------------------------------------------------


def test_run_raises_when_swebench_not_installed(tmp_path):
    """run() must raise RuntimeError when the swebench extra is absent.

    Importantly, this test uses a populated env (has instance_ids) so that
    the instance_ids guard passes and the swebench-missing guard fires.
    """
    import sys
    import unittest.mock as mock

    from clousight_bench.core.suite import DriverContext, EnvHandle, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    env = EnvHandle({"instance_ids": ["django__django-11099"], "agent_kind": "gold"})
    driver = DriverContext(placement="local")

    # Simulate swebench not importable
    with mock.patch.dict(sys.modules, {"swebench": None}):
        import importlib.util

        with mock.patch.object(importlib.util, "find_spec", return_value=None):
            import pytest

            with pytest.raises(RuntimeError, match="swebench extra not installed"):
                suite.run(target, env, driver)


def test_run_raises_when_placement_not_local(tmp_path):
    """run() must raise RuntimeError when placement != 'local', even if swebench is importable."""
    import importlib.util
    import types
    import unittest.mock as mock

    import pytest

    from clousight_bench.core.suite import DriverContext, EnvHandle, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    env = EnvHandle({"instance_ids": ["django__django-11099"], "agent_kind": "gold"})
    driver = DriverContext(placement="in_cloud")

    # Patch find_spec to return a sentinel so the swebench guard passes,
    # letting execution reach the placement check.
    sentinel = types.ModuleType("swebench")
    with mock.patch.object(importlib.util, "find_spec", return_value=sentinel):
        with pytest.raises(RuntimeError, match="driver.placement must be 'local'"):
            suite.run(target, env, driver)


# ---------------------------------------------------------------------------
# Task 2: resolve carries agent_kind, prepare materializes payload, run guards
# ---------------------------------------------------------------------------


def test_resolve_carries_agent_kind() -> None:
    """resolve() must forward agent_kind from cfg into the dataset payload."""
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    dh = suite.resolve({"agent_kind": "empty", "instance_ids": ["django__django-11099"]}, None)
    assert dh.payload["agent_kind"] == "empty"


def test_resolve_agent_kind_defaults_to_gold() -> None:
    """When agent_kind is absent from cfg, resolve() defaults to 'gold'."""
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    dh = suite.resolve({"instance_ids": ["django__django-11099"]}, None)
    assert dh.payload["agent_kind"] == "gold"


def test_prepare_materializes_payload(tmp_path) -> None:
    """prepare() returns an EnvHandle with all 7 documented keys."""
    import os

    from clousight_bench.core.suite import DatasetHandle, DriverContext, EnvHandle, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    driver = DriverContext(placement="local")
    instance_ids = ["django__django-11099", "sympy__sympy-20590"]
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": instance_ids, "agent_kind": "gold"},
    )

    env = suite.prepare(target, dataset, driver)

    assert isinstance(env, EnvHandle)
    # All 7 documented keys must be present
    required_keys = {
        "instance_ids",
        "agent_kind",
        "_tmp_dir",
        "dataset_name",
        "split",
        "run_id",
        "harness_timeout_s",
    }
    assert required_keys == set(env.payload.keys()), (
        f"Missing keys: {required_keys - set(env.payload.keys())}"
    )

    # instance_ids must match dataset's
    assert env.payload["instance_ids"] == instance_ids

    # The pinned constants downstream tasks depend on — exact values, not just presence.
    assert env.payload["dataset_name"] == "princeton-nlp/SWE-bench_Verified"
    assert env.payload["split"] == "test"
    assert env.payload["harness_timeout_s"] == 3600.0

    # _tmp_dir must exist on disk
    assert os.path.isdir(env.payload["_tmp_dir"]), "_tmp_dir does not exist on disk"

    # run_id must start with "csbench-"
    assert env.payload["run_id"].startswith("csbench-"), (
        f"run_id does not start with 'csbench-': {env.payload['run_id']!r}"
    )


def test_run_raises_on_empty_instance_ids() -> None:
    """run() must raise RuntimeError about instance_ids even when swebench is NOT installed.

    The check order must be: instance_ids guard FIRST, then swebench installed check,
    then placement check.  This test verifies the guard fires before the extra check.
    """
    import importlib.util
    import unittest.mock as mock

    import pytest

    from clousight_bench.core.suite import DriverContext, EnvHandle, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    # Empty payload — no instance_ids key at all
    env = EnvHandle({})
    driver = DriverContext(placement="local")

    # Patch find_spec to None so swebench appears absent;
    # the instance_ids guard must fire BEFORE the swebench check.
    with mock.patch.object(importlib.util, "find_spec", return_value=None):
        with pytest.raises(RuntimeError, match="no instance_ids"):
            suite.run(target, env, driver)


# ---------------------------------------------------------------------------
# Task 3: upstream harness invocation fidelity
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent.parent / "src" / "clousight_bench" / "suites" / "swe_bench" / "fixtures"
_BUNDLED_IDS = [
    "django__django-11099",
    "sympy__sympy-20590",
    "pytest-dev__pytest-7205",
]


def test_normalize_upstream_report_shape_contract():
    """_normalize_upstream_report must produce the exact canonical results.json shape."""
    import json

    from clousight_bench.suites.swe_bench.suite import _normalize_upstream_report

    sample = json.loads((_FIXTURES_DIR / "upstream_report_sample.json").read_text())
    canonical = json.loads((_FIXTURES_DIR / "results.json").read_text())

    result = _normalize_upstream_report(sample, _BUNDLED_IDS)

    # Top-level keys must match exactly
    assert set(result.keys()) == set(canonical.keys()), (
        f"key mismatch: got {set(result.keys())}, want {set(canonical.keys())}"
    )

    # per_instance: same instance ids, each value is {"resolved": bool}
    assert set(result["per_instance"].keys()) == set(canonical["per_instance"].keys())
    for iid, v in result["per_instance"].items():
        assert set(v.keys()) == {"resolved"}, f"per_instance[{iid!r}] has unexpected keys: {v}"
        assert isinstance(v["resolved"], bool), f"per_instance[{iid!r}]['resolved'] is not bool"

    # resolved_ids in sample: django + pytest-dev → 2 resolved, sympy not → total 3
    assert result["resolved"] == 2
    assert result["total"] == 3

    # Spot-check per_instance values against the sample's resolved_ids
    assert result["per_instance"]["django__django-11099"]["resolved"] is True
    assert result["per_instance"]["sympy__sympy-20590"]["resolved"] is False
    assert result["per_instance"]["pytest-dev__pytest-7205"]["resolved"] is True


def test_normalize_rejects_unknown_shape():
    """_normalize_upstream_report must raise RuntimeError for an unrecognized report shape."""
    import pytest

    from clousight_bench.suites.swe_bench.suite import _normalize_upstream_report

    with pytest.raises(RuntimeError, match="unrecognized swebench report shape"):
        _normalize_upstream_report({"foo": 1}, _BUNDLED_IDS)


def _make_prepared_env(tmp_path: Path, instance_ids: list[str] | None = None) -> Any:
    """Build an EnvHandle as prepare() would, writing run_id-based payload."""
    import hashlib
    import json

    from clousight_bench.core.suite import EnvHandle

    ids = instance_ids if instance_ids is not None else _BUNDLED_IDS
    run_id = "csbench-" + hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()[:8]
    return EnvHandle(
        {
            "instance_ids": ids,
            "agent_kind": "gold",
            "_tmp_dir": str(tmp_path),
            "dataset_name": "princeton-nlp/SWE-bench_Verified",
            "split": "test",
            "run_id": run_id,
            "harness_timeout_s": 3600.0,
        }
    )


def _make_fake_report(tmp_path: Path, run_id: str, model_name: str | None = None) -> None:
    """Write a fake upstream report at the exact path the harness would produce."""
    import json

    from clousight_bench.suites.swe_bench.suite import _MODEL_NAME

    report_name = f"{model_name or _MODEL_NAME}.{run_id}.json"
    report = {
        "schema_version": 2,
        "total_instances": 3,
        "resolved_instances": 2,
        "submitted_instances": 3,
        "resolved_ids": ["django__django-11099", "pytest-dev__pytest-7205"],
        "unresolved_ids": ["sympy__sympy-20590"],
        "submitted_ids": _BUNDLED_IDS,
        "error_ids": [],
        "empty_patch_ids": [],
    }
    (tmp_path / report_name).write_text(json.dumps(report))


def test_predictions_carry_model_name(tmp_path):
    """run() must write model_name_or_path in every predictions.jsonl line,
    call subprocess with --dataset_name, --split, --report_dir, sys.executable argv[0],
    and read the report from the exact expected path."""
    import importlib.util
    import json
    import subprocess
    import sys
    import types
    import unittest.mock as mock

    from clousight_bench.core.suite import DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import _MODEL_NAME, SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    env = _make_prepared_env(tmp_path)
    driver = DriverContext(placement="local")
    run_id = env.payload["run_id"]

    captured_cmd: list[list[str]] = []

    def fake_subprocess_run(cmd, **kwargs):  # type: ignore[override]
        captured_cmd.append(list(cmd))
        # Write the fake report at the exact expected path
        _make_fake_report(tmp_path, run_id)
        result = mock.MagicMock()
        result.returncode = 0
        return result

    sentinel = types.ModuleType("swebench")
    with mock.patch.object(importlib.util, "find_spec", return_value=sentinel):
        with mock.patch.object(subprocess, "run", side_effect=fake_subprocess_run):
            ra = suite.run(target, env, driver)

    # Verify predictions.jsonl has model_name_or_path on every line
    predictions_path = tmp_path / "predictions.jsonl"
    assert predictions_path.exists()
    lines = [ln for ln in predictions_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == len(_BUNDLED_IDS)
    for line in lines:
        record = json.loads(line)
        assert record.get("model_name_or_path") == _MODEL_NAME, (
            f"model_name_or_path missing or wrong: {record}"
        )

    # Verify subprocess cmd structure
    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    assert cmd[0] == sys.executable, f"argv[0] must be sys.executable, got {cmd[0]!r}"
    assert "--dataset_name" in cmd
    assert cmd[cmd.index("--dataset_name") + 1] == "princeton-nlp/SWE-bench_Verified"
    assert "--split" in cmd
    assert cmd[cmd.index("--split") + 1] == "test"
    assert "--report_dir" in cmd
    assert "--run_id" in cmd
    assert "--instance_ids" in cmd

    # run() should return RawArtifacts without error
    assert ra is not None


# ---------------------------------------------------------------------------
# B-slice-2 Task 3: real SUT path — prepare provisions, run solves, teardown closes
# ---------------------------------------------------------------------------


class _StubSutTransport:
    """Transport stub with the ``_invoke`` seam; echoes the gold patch back."""

    def __init__(self) -> None:
        self._last_trace_id = "stub-trace"

    def _invoke(self, session_id: str, openai_body: dict) -> dict:
        from clousight_bench.domains.agent_runtime import protocol

        swe = protocol.decode_request(openai_body)["swe"]
        span = {
            "trace_id": "t" * 32,
            "span_id": "s" * 16,
            "parent_span_id": "",
            "name": "swe-oracle",
            "kind": "CHAIN",
            "status": "ok",
            "attributes": {
                "openinference.span.kind": "CHAIN",
                "swe.instance_id": swe["instance_id"],
            },
        }
        return protocol.encode_result(
            {
                "model_patch": f"diff --git stub-patch-{swe['instance_id']}",
                "usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
                "_spans": [span],
            }
        )


class _StubSutAdapter:
    """Adapter stub mirroring the surface SweSutClient uses."""

    def __init__(self) -> None:
        self._t = _StubSutTransport()
        self.provisioned = 0
        self.provision_specs: list[dict] = []
        self.deprovisioned: list[str] = []
        self._seq = 0

    def transport(self):
        return self._t

    def create_session(self, spec=None):
        self._seq += 1
        return f"sess-{self._seq}"

    def destroy_session(self, session_id):
        return None

    def provision(self, spec=None):
        import types

        self.provisioned += 1
        self.provision_specs.append(dict(spec or {}))
        return types.SimpleNamespace(runtime_id="rt-stub")

    def deprovision(self, runtime_id):
        import types

        self.deprovisioned.append(runtime_id)
        return types.SimpleNamespace()


def test_prepare_nonmock_with_adapter_provisions_sut(tmp_path):
    """prepare() with mock=False and a Target.handle adapter provisions the SUT
    and stashes the client under the private '_sut' payload key."""
    from clousight_bench.core.suite import DatasetHandle, DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite
    from clousight_bench.suites.swe_bench.sut_client import SweSutClient

    adapter = _StubSutAdapter()
    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False, handle=adapter)
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": ["django__django-11099"], "agent_kind": "oracle"},
    )
    env = suite.prepare(target, dataset, DriverContext(placement="local"))
    assert isinstance(env.payload["_sut"], SweSutClient)
    assert adapter.provisioned == 1
    # The 7 documented keys are all still present alongside _sut.
    assert {
        "instance_ids",
        "agent_kind",
        "_tmp_dir",
        "dataset_name",
        "split",
        "run_id",
        "harness_timeout_s",
    } <= set(env.payload.keys())


def test_prepare_llm_mode_forwards_driver_key_into_provision_env(tmp_path, monkeypatch):
    """prepare() knows agent_kind — llm mode threads the DRIVER-held
    DASHSCOPE_API_KEY into the provision spec's environment_variables (B2)."""
    from clousight_bench.core.suite import DatasetHandle, DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-driver-held-key")
    adapter = _StubSutAdapter()
    target = Target(mode="endpoint", mock=False, handle=adapter)
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": ["django__django-11099"], "agent_kind": "llm"},
    )
    SweBenchSuite().prepare(target, dataset, DriverContext(placement="local"))
    assert adapter.provision_specs == [{"environment_variables": {"DASHSCOPE_API_KEY": "sk-driver-held-key"}}]


def test_prepare_oracle_mode_provisions_without_llm_key(tmp_path, monkeypatch):
    """oracle mode never forwards the key, even when the driver env carries one."""
    from clousight_bench.core.suite import DatasetHandle, DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-driver-held-key")
    adapter = _StubSutAdapter()
    target = Target(mode="endpoint", mock=False, handle=adapter)
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": ["django__django-11099"], "agent_kind": "oracle"},
    )
    SweBenchSuite().prepare(target, dataset, DriverContext(placement="local"))
    assert adapter.provision_specs == [{}]


def test_prepare_nonmock_without_adapter_has_no_sut(tmp_path):
    """The gated docker smoke drives prepare() with mock=False and handle=None —
    no SUT is provisioned and run() falls back to the MockAgent."""
    from clousight_bench.core.suite import DatasetHandle, DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False, handle=None)
    dataset = DatasetHandle(
        version="v0",
        digest="sha256:x",
        payload={"instance_ids": ["django__django-11099"], "agent_kind": "gold"},
    )
    env = suite.prepare(target, dataset, DriverContext(placement="local"))
    assert "_sut" not in env.payload


class _RecordingSut:
    """Hand-stubbed SweSutClient for run()/teardown() tests."""

    def __init__(self, fail_close: bool = False) -> None:
        self.solved: list[tuple[str, str]] = []
        self.closed = 0
        self._fail_close = fail_close

    def solve(self, instance: dict, agent_mode: str) -> dict:
        iid = instance["instance_id"]
        self.solved.append((iid, agent_mode))
        span = {
            "span_id": f"span-{iid}",
            "trace_id": f"trace-{iid}",
            "parent_id": None,
            "name": "swe-oracle",
            "kind": "tool_call",
            "t_start": 1000.0,
            "t_end": 1001.0,
            "status": "ok",
            "attrs": {"swe.instance_id": iid},
        }
        return {
            "model_patch": f"diff --git real-patch-{iid}",
            "spans": [span],
            "usage_events": [{"kind": "llm_tokens", "value": 100, "instance_id": iid, "mode": agent_mode}],
        }

    def close(self) -> None:
        self.closed += 1
        if self._fail_close:
            raise RuntimeError("close boom")


def _run_real_path(tmp_path):
    """Drive run() with a recording SUT stub + faked harness; return (sut, ra)."""
    import importlib.util
    import subprocess
    import types
    import unittest.mock as mock

    from clousight_bench.core.suite import DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    env = _make_prepared_env(tmp_path)
    env.payload["agent_kind"] = "oracle"
    sut = _RecordingSut()
    env.payload["_sut"] = sut
    run_id = env.payload["run_id"]

    def fake_subprocess_run(cmd, **kwargs):
        # Real path (agent_kind=oracle): the report filename is mode-derived.
        _make_fake_report(tmp_path, run_id, model_name="csbench-oracle-agent")
        result = mock.MagicMock()
        result.returncode = 0
        return result

    sentinel = types.ModuleType("swebench")
    with mock.patch.object(importlib.util, "find_spec", return_value=sentinel):
        with mock.patch.object(subprocess, "run", side_effect=fake_subprocess_run):
            ra = suite.run(target, env, DriverContext(placement="local"))
    return sut, ra


def test_run_real_path_writes_real_predictions_trajectory_usage(tmp_path):
    """run() with a '_sut' client writes REAL artifacts — the canned trajectory
    and usage fixtures must NOT appear on this path."""
    import json

    from clousight_bench.core.sut_span import validate_span

    sut, ra = _run_real_path(tmp_path)

    # Every instance was solved in oracle mode.
    assert sut.solved == [(iid, "oracle") for iid in _BUNDLED_IDS]

    # predictions.jsonl carries the SUT's patch text and the MODE-DERIVED label —
    # a real oracle run must never be labeled as the mock agent.
    pred_lines = [json.loads(ln) for ln in (tmp_path / "predictions.jsonl").read_text().splitlines()]
    assert [p["instance_id"] for p in pred_lines] == _BUNDLED_IDS
    for p in pred_lines:
        assert p["model_patch"] == f"diff --git real-patch-{p['instance_id']}"
        assert p["model_name_or_path"] == "csbench-oracle-agent"

    # trajectory.jsonl is the REAL spans (validating v2), not the canned fixture.
    traj_text = (tmp_path / "trajectory.jsonl").read_text()
    assert "trace-mock-0001" not in traj_text  # canned fixture marker absent
    traj_lines = [json.loads(ln) for ln in traj_text.splitlines() if ln.strip()]
    assert len(traj_lines) == len(_BUNDLED_IDS)
    for span in traj_lines:
        validate_span(span)
    assert {s["attrs"]["swe.instance_id"] for s in traj_lines} == set(_BUNDLED_IDS)

    # usage.jsonl carries one llm_tokens event per instance.
    usage_lines = [json.loads(ln) for ln in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert usage_lines == [
        {"kind": "llm_tokens", "value": 100, "instance_id": iid, "mode": "oracle"} for iid in _BUNDLED_IDS
    ]

    # manifest reflects the real files.
    assert ra.manifest["trajectory"]["rows"] == len(_BUNDLED_IDS)
    assert ra.manifest["usage"]["rows"] == len(_BUNDLED_IDS)


def test_run_real_path_rejects_mock_agent_kinds(tmp_path):
    """A real SUT run with a mock-agent agent_kind (e.g. the default 'gold') must
    fail loudly before any instance is solved — never a silent paid run."""
    import importlib.util
    import types
    import unittest.mock as mock

    import pytest

    from clousight_bench.core.suite import DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    env = _make_prepared_env(tmp_path)  # agent_kind defaults to "gold"
    sut = _RecordingSut()
    env.payload["_sut"] = sut

    sentinel = types.ModuleType("swebench")
    with mock.patch.object(importlib.util, "find_spec", return_value=sentinel):
        with pytest.raises(RuntimeError, match="oracle' or 'llm"):
            suite.run(Target(mode="endpoint", mock=False), env, DriverContext(placement="local"))
    assert sut.solved == []  # guard fires before the instance loop


def test_run_real_path_usage_feeds_evaluator_cost(tmp_path):
    """The real usage.jsonl is consumed by the official evaluator into a cost
    measurement (cost_per_resolved appears alongside resolved)."""
    from clousight_bench.suites.swe_bench.evaluator import OfficialSweEvaluator

    _, ra = _run_real_path(tmp_path)
    out = OfficialSweEvaluator().evaluate(ra)
    assert "swe-bench.resolved" in out
    assert "swe-bench.cost_per_resolved" in out
    assert out["swe-bench.cost_per_resolved"].value > 0


def test_teardown_closes_sut_before_docker_cleanup():
    """teardown() closes the SUT client (best-effort) even without a run_id/docker."""
    from clousight_bench.core.suite import EnvHandle
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    sut = _RecordingSut()
    SweBenchSuite().teardown(EnvHandle({"_sut": sut}))
    assert sut.closed == 1


def test_teardown_swallows_sut_close_failure():
    """A raising sut.close() must not break teardown (best-effort by contract)."""
    from clousight_bench.core.suite import EnvHandle
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    sut = _RecordingSut(fail_close=True)
    result = SweBenchSuite().teardown(EnvHandle({"_sut": sut, "run_id": ""}))
    assert result is None
    assert sut.closed == 1


def test_run_raises_with_stderr_tail(tmp_path):
    """run() must raise RuntimeError containing stderr content when harness exits non-zero."""
    import importlib.util
    import subprocess
    import types
    import unittest.mock as mock

    import pytest

    from clousight_bench.core.suite import DriverContext, Target
    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    target = Target(mode="endpoint", mock=False)
    env = _make_prepared_env(tmp_path)
    driver = DriverContext(placement="local")

    boom_stderr = "boom" * 10

    def fake_subprocess_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=boom_stderr)

    sentinel = types.ModuleType("swebench")
    with mock.patch.object(importlib.util, "find_spec", return_value=sentinel):
        with mock.patch.object(subprocess, "run", side_effect=fake_subprocess_run):
            with pytest.raises(RuntimeError, match="boom"):
                suite.run(target, env, driver)
