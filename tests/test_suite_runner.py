"""Tests for SuiteRunner — the Task adapter that wraps a BenchmarkSuite + Evaluator."""

import inspect
import json
import tempfile
from pathlib import Path

import pytest

from clousight_bench.core.observation import Measurement
from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    EnvHandle,
    Evaluator,
    RawArtifacts,
)
from clousight_bench.core.suite_runner import SuiteRunner


class _Suite(BenchmarkSuite):
    suite_id = "demo"
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
        return RawArtifacts(d, {"results": {"path": "r.json", "sha256": "x", "rows": None}})


class _Eval(Evaluator):
    evaluator_id = "demo-eval"
    official = True

    def supports(self, suite_id, product):
        return suite_id == "demo"

    def evaluate(self, raw):
        import json

        r = json.loads(raw.path("results").read_text())
        return {
            "demo.resolved": Measurement(
                r["resolved"] / r["total"],
                "ratio",
                reproducibility_class="deterministic",
                official=True,
            )
        }


def test_suite_task_execute_then_score(tmp_path):
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})
    result = st.score(bundle)
    m = result.measurements
    assert abs(m["demo.resolved"].value - 0.5) < 1e-9


def test_suite_task_provenance():
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    p = st.provenance()
    assert p.suite_id == "demo" and p.suite_version == "v1"
    assert p.evaluator_id == "demo-eval" and p.evaluator_official is True and p.unmodified is True


def test_provenance_scaffold_comes_from_the_suite():
    """R3: scaffold is sourced from suite.scaffold(), not hardcoded in core. A
    plain suite (no agent scaffold) yields "" in both mock and real modes — the
    non-agent-suite mis-tagging bug (previously always "mock-agent@slice1") is
    fixed."""
    assert SuiteRunner(_Suite(), _Eval(), mock=True, params={}).provenance().scaffold == ""
    assert (
        SuiteRunner(_Suite(), _Eval(), mock=False, params={"agent_kind": "oracle"}).provenance().scaffold == ""
    )


def test_provenance_scaffold_flows_a_suite_override():
    """A suite that overrides scaffold() drives Provenance.scaffold."""

    class _ScaffoldSuite(_Suite):
        def scaffold(self, params, *, mock):
            return "mock-x" if mock else f"real-{params.get('agent_kind', '')}"

    assert SuiteRunner(_ScaffoldSuite(), _Eval(), mock=True).provenance().scaffold == "mock-x"
    assert (
        SuiteRunner(_ScaffoldSuite(), _Eval(), mock=False, params={"agent_kind": "llm"}).provenance().scaffold
        == "real-llm"
    )


def test_suite_task_id_is_namespaced():
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    assert st.task_id == "suite:demo"


def test_suite_task_config_contains_identity():
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    cfg = st.config({"foo": "bar"})
    assert cfg["suite_id"] == "demo"
    assert cfg["suite_version"] == "v1"
    assert cfg["evaluator_id"] == "demo-eval"
    assert cfg["foo"] == "bar"


def test_suite_task_execute_stores_manifest_in_bundle(tmp_path):
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})
    assert "artifacts_subdir" in bundle.observations
    assert "manifest" in bundle.observations
    assert "results" in bundle.observations["manifest"]


def test_suite_task_mock_does_not_call_run(tmp_path):
    """run() would raise AssertionError — verifies mock path is used."""
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    bundle = st.execute(adapter=None, params={"_tmp": str(tmp_path)})
    assert bundle is not None  # no AssertionError from _Suite.run


def test_suite_task_execute_uses_constructor_params_for_resolve(tmp_path):
    """Non-mock execute resolves using constructor params only — call-time params do NOT
    reach resolve().  Fingerprint stability requires the dataset digest to be fixed at
    construction time (the bridge passes spec.params into the constructor, so in the real
    path the two dicts are the same).  Call-time params are still passed to
    mock_artifacts / prepare / run for any other purpose but are NOT merged into resolve().
    """
    received: list[dict] = []

    class _CaptureSuite(_Suite):
        def resolve(self, cfg, assets):
            received.append(dict(cfg))
            return DatasetHandle("v1", "sha256:d", {})

        def run(self, target, env, driver):
            d = Path(tmp_path)
            (d / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(d, {"results": {"path": "r.json", "sha256": "x", "rows": None}})

    st = SuiteRunner(_CaptureSuite(), _Eval(), mock=False, params={"agent_kind": "gold", "keep": "me"})
    st.execute(adapter=None, params={"agent_kind": "empty"})
    # resolve() is called with constructor params only — call-time params do NOT win
    assert received == [{"agent_kind": "gold", "keep": "me"}]


def test_base_task_provenance_empty():
    """A plain non-suite Task's provenance() returns the empty default."""
    from clousight_bench.core.observation import ObservationBundle, TaskResult
    from clousight_bench.core.plugin import Task

    class _StubTask(Task):
        task_id = "stub"

        def config(self, params):
            return {}

        def execute(self, adapter, params):
            return ObservationBundle(observations={}, artifacts=[])

        def score(self, observations):
            return TaskResult(measurements={})

    assert _StubTask().provenance().is_empty()


def test_suite_provenance_has_real_digest():
    """SuiteRunner.provenance().dataset_digest equals the suite's resolved digest (non-empty)."""
    st = SuiteRunner(_Suite(), _Eval(), params={"instance_ids": ["a", "b"]})
    p = st.provenance()
    expected = _Suite().resolve({"instance_ids": ["a", "b"]}, None).digest
    assert p.dataset_digest == expected
    assert p.dataset_digest.startswith("sha256:")


def test_fingerprint_moves_with_dataset_digest():
    """Two SuiteRunners with different instance_ids produce different benchmark fingerprints."""
    from clousight_bench.core.fingerprints import benchmark_fingerprint

    class _VaryingSuite(_Suite):
        def resolve(self, cfg, assets):
            ids = cfg.get("instance_ids", [])
            digest = "sha256:" + "-".join(sorted(ids))
            return DatasetHandle("v1", digest, {})

    st_a = SuiteRunner(_VaryingSuite(), _Eval(), params={"instance_ids": ["a"]})
    st_b = SuiteRunner(_VaryingSuite(), _Eval(), params={"instance_ids": ["b"]})

    fp_a = benchmark_fingerprint(
        task_id=st_a.task_id,
        task_revision=st_a.task_revision,
        scorer_revision=st_a.scorer_revision,
        workload="",
        workload_version="",
        assets=[],
        params={},
        provenance=st_a.provenance().to_dict(),
    )
    fp_b = benchmark_fingerprint(
        task_id=st_b.task_id,
        task_revision=st_b.task_revision,
        scorer_revision=st_b.scorer_revision,
        workload="",
        workload_version="",
        assets=[],
        params={},
        provenance=st_b.provenance().to_dict(),
    )
    assert fp_a != fp_b


def test_resolve_called_once(tmp_path):
    """provenance() then execute() triggers exactly one resolve() call (cached)."""
    call_count = 0

    class _CountingSuite(_Suite):
        def resolve(self, cfg, assets):
            nonlocal call_count
            call_count += 1
            return DatasetHandle("v1", "sha256:counted", {})

        def run(self, target, env, driver):
            d = Path(tmp_path)
            (d / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(d, {"results": {"path": "r.json", "sha256": "x", "rows": None}})

    st = SuiteRunner(_CountingSuite(), _Eval(), mock=False, params={})
    st.provenance()  # first call — should trigger resolve
    st.execute(adapter=None, params={})  # second call — should reuse cached handle
    assert call_count == 1


# ---------------------------------------------------------------------------
# Task 5: portable staged artifacts
# ---------------------------------------------------------------------------


def test_execute_stages_into_artifacts_root(tmp_path):
    """Files are copied into artifacts_root/<subdir>/, observations carry artifacts_subdir
    (no raw_dir key), and the suite's original temp dir is gone after execute."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    artifacts_root = tmp_path / "arts"
    artifacts_root.mkdir()

    class _SuiteWithKnownRaw(_Suite):
        def mock_artifacts(self, cfg):
            (raw_dir / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(
                raw_dir,
                {"results": {"path": "r.json", "sha256": "x", "rows": None}},
            )

    st = SuiteRunner(_SuiteWithKnownRaw(), _Eval(), mock=True, artifacts_root=artifacts_root)
    bundle = st.execute(adapter=None, params={})

    # observations must have artifacts_subdir, NOT raw_dir
    assert "artifacts_subdir" in bundle.observations, (
        f"expected 'artifacts_subdir' key, got {list(bundle.observations)}"
    )
    assert "raw_dir" not in bundle.observations, "raw_dir must be removed from observations"
    assert "manifest" in bundle.observations

    # staged files must exist under artifacts_root/<subdir>/
    subdir = bundle.observations["artifacts_subdir"]
    stage_dir = artifacts_root / subdir
    assert stage_dir.is_dir(), f"stage_dir {stage_dir} does not exist"
    assert (stage_dir / "r.json").exists(), "staged file r.json missing"

    # original temp dir must be gone
    assert not raw_dir.exists(), f"raw_dir {raw_dir} should have been deleted after staging"


def test_no_absolute_paths_in_bundle(tmp_path):
    """After execute(), a JSON dump of the bundle contains no absolute paths."""
    artifacts_root = tmp_path / "arts"
    artifacts_root.mkdir()

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    class _SuiteWithKnownRaw(_Suite):
        def mock_artifacts(self, cfg):
            (raw_dir / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(
                raw_dir,
                {"results": {"path": "r.json", "sha256": "x", "rows": None}},
            )

    st = SuiteRunner(_SuiteWithKnownRaw(), _Eval(), mock=True, artifacts_root=artifacts_root)
    bundle = st.execute(adapter=None, params={})

    bundle_text = json.dumps({"observations": bundle.observations, "artifacts": bundle.artifacts})

    # Must not contain the tmp_path prefix (explicit artifacts root)
    assert str(tmp_path) not in bundle_text, (
        f"bundle_text contains absolute path {tmp_path}: {bundle_text[:300]}"
    )
    # Must not contain gettempdir (fallback root leak)
    tmp_gettempdir = tempfile.gettempdir()
    tmp_resolved = str(Path(tmp_gettempdir).resolve())
    assert tmp_gettempdir not in bundle_text, (
        f"bundle_text contains tempfile.gettempdir() path: {bundle_text[:300]}"
    )
    assert tmp_resolved not in bundle_text, f"bundle_text contains resolved tmpdir path: {bundle_text[:300]}"


def test_score_roundtrip_from_staged(tmp_path):
    """execute() then score() on the same SuiteRunner yields the expected measurement."""
    artifacts_root = tmp_path / "arts"
    artifacts_root.mkdir()

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()

    class _SuiteWithKnownRaw(_Suite):
        def mock_artifacts(self, cfg):
            (raw_dir / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(
                raw_dir,
                {"results": {"path": "r.json", "sha256": "x", "rows": None}},
            )

    st = SuiteRunner(_SuiteWithKnownRaw(), _Eval(), mock=True, artifacts_root=artifacts_root)
    bundle = st.execute(adapter=None, params={})
    result = st.score(bundle)

    assert "demo.resolved" in result.measurements
    assert abs(result.measurements["demo.resolved"].value - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# Task 8: contract cleanup tests
# ---------------------------------------------------------------------------


def test_evaluate_signature_no_telemetry() -> None:
    """Evaluator.evaluate must accept exactly (self, raw) — no telemetry param."""
    from clousight_bench.core.suite import Evaluator

    for target in (Evaluator.evaluate, _Eval.evaluate):  # the ABC contract AND the local fake
        params = list(inspect.signature(target).parameters)
        assert params == ["self", "raw"], (
            f"evaluate() must have exactly (self, raw), got {params} on {target!r}"
        )


def test_suite_task_revision_from_suite() -> None:
    """SuiteRunner.task_revision equals the suite's suite_version (not the default '0')."""
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    assert st.task_revision == _Suite.suite_version
    assert st.task_revision == "v1"


def test_workload_identity_from_suite() -> None:
    """workload_identity() returns suite_id, suite_version, and dataset digest."""
    st = SuiteRunner(_Suite(), _Eval(), mock=True)
    wi = st.workload_identity({})
    assert wi["workload"] == "demo"
    assert wi["workload_version"] == "v1"
    assert isinstance(wi["assets"], list)
    assert len(wi["assets"]) == 1
    assert wi["assets"][0].startswith("sha256:")


def test_teardown_noop_without_run_id(monkeypatch, tmp_path) -> None:
    """SuiteRunner.teardown with empty payload never calls docker."""
    import subprocess as _subprocess

    calls: list = []
    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: calls.append(a))

    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    suite.teardown(EnvHandle({}))  # no run_id
    assert not calls, f"docker must not be called when run_id is absent: {calls}"


def test_teardown_scoped_filter(monkeypatch, tmp_path) -> None:
    """teardown calls docker ps with --filter name=<run_id> and rm -f only those IDs."""
    import shutil as _shutil
    import subprocess as _subprocess

    captured: list[list[str]] = []

    class _FakeCompleted:
        stdout = "abc123\n"
        returncode = 0

    def _fake_run(cmd, **kw):
        captured.append(list(cmd))
        return _FakeCompleted()

    monkeypatch.setattr(_subprocess, "run", _fake_run)
    monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/docker")

    from clousight_bench.suites.swe_bench.suite import SweBenchSuite

    suite = SweBenchSuite()
    suite.teardown(EnvHandle({"run_id": "csbench-aabbccdd"}))

    assert any("--filter" in cmd and "name=csbench-aabbccdd" in cmd for cmd in captured), (
        f"Expected --filter name=<run_id> in docker ps call: {captured}"
    )
    assert any("rm" in cmd and "-f" in cmd and "abc123" in cmd for cmd in captured), (
        f"Expected docker rm -f abc123 call: {captured}"
    )
    # The destructive verbs must NEVER appear — teardown is container-scoped by contract.
    assert all(not ({"rmi", "prune", "system"} & set(cmd)) for cmd in captured), (
        f"teardown must never touch images or system-wide state: {captured}"
    )


def test_record_from_suite_run_validates_against_tightened_schema(monkeypatch, tmp_path) -> None:
    """A ResultRecord produced from a SuiteRunner mock run validates against schema 0.4."""
    jsonschema = pytest.importorskip("jsonschema")

    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.plugin import DomainPack, ProviderAdapter
    from clousight_bench.core.schema import RunSpec

    class _SuiteForSchema(_Suite):
        def mock_artifacts(self, cfg):
            import json as _json

            d = Path(tempfile.mkdtemp())
            (d / "r.json").write_text('{"resolved": 1, "total": 2}')
            span = {
                "span_id": "s1",
                "trace_id": "t1",
                "parent_id": None,
                "name": "step",
                "kind": "tool_call",
                "t_start": 1.0,
                "t_end": 2.0,
                "status": "ok",
                "attrs": {},
            }
            (d / "traj.jsonl").write_text(_json.dumps(span) + "\n")
            return RawArtifacts(
                d,
                {
                    "results": {"path": "r.json", "sha256": "x", "rows": None},
                    "trajectory": {"path": "traj.jsonl", "sha256": "y", "rows": 1},
                },
            )

    class _Adapter(ProviderAdapter):
        name = "fake"
        status = "reference"

    class _Domain(DomainPack):
        domain = "schema-test-domain"

        def tasks(self):
            return {}

        def adapters(self):
            return {"fake": _Adapter}

    import clousight_bench.core.registry as _reg

    suite_inst = _SuiteForSchema()
    eval_inst = _Eval()

    monkeypatch.setattr("clousight_bench.core.store.STORE_AVAILABLE", False)
    monkeypatch.setattr(orch, "get_domain", lambda name: _Domain())
    monkeypatch.setattr(_reg, "load_benchmark_suites", lambda: {"demo": suite_inst})
    monkeypatch.setattr(_reg, "load_evaluators", lambda: [eval_inst])

    rec = orch.execute(
        RunSpec("schema-test-domain", "suite:demo", "fake"),
        results_dir=tmp_path,
        enrich=False,
    )

    import json
    from pathlib import Path as _Path

    schema_path = (
        _Path(__file__).parent.parent
        / "src"
        / "clousight_bench"
        / "resources"
        / "schemas"
        / "result-record-0.4.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    record_dict = json.loads(json.dumps(rec.to_dict()))

    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(record_dict))
    assert not errors, f"Schema validation failed: {[str(e) for e in errors]}"

    # The tightening must REJECT malformed inner shapes — otherwise reverting the
    # measurements/artifacts sub-schemas to bare object/array would go unnoticed.
    import copy

    broken_measurement = copy.deepcopy(record_dict)
    first_key = next(iter(broken_measurement["measurements"]))
    del broken_measurement["measurements"][first_key]["unit"]
    assert list(validator.iter_errors(broken_measurement)), (
        "schema accepted a measurement missing 'unit' — tightening regressed"
    )

    broken_extra = copy.deepcopy(record_dict)
    first_key = next(iter(broken_extra["measurements"]))
    broken_extra["measurements"][first_key]["surprise"] = 1
    assert list(validator.iter_errors(broken_extra)), (
        "schema accepted an unknown measurement key — additionalProperties regressed"
    )

    broken_artifact = copy.deepcopy(record_dict)
    assert broken_artifact["artifacts"], "expected the mock run to surface at least one artifact"
    del broken_artifact["artifacts"][0]["sha256"]
    assert list(validator.iter_errors(broken_artifact)), (
        "schema accepted an artifact missing 'sha256' — tightening regressed"
    )


def test_execute_nonmock_calls_teardown_even_when_run_raises(tmp_path) -> None:
    """The non-mock path must teardown(env) in a finally — including on run() failure."""
    calls: list[str] = []

    class _TeardownSuite(_Suite):
        def prepare(self, target, dataset, driver):
            calls.append("prepare")
            return EnvHandle({"run_id": "csbench-test"})

        def run(self, target, env, driver):
            calls.append("run")
            raise RuntimeError("harness exploded")

        def teardown(self, env):
            calls.append(f"teardown:{env.payload.get('run_id', '')}")

    st = SuiteRunner(_TeardownSuite(), _Eval(), mock=False, artifacts_root=tmp_path)
    with pytest.raises(RuntimeError, match="harness exploded"):
        st.execute(adapter=None, params={})
    assert calls == ["prepare", "run", "teardown:csbench-test"]


def test_execute_nonmock_calls_teardown_on_success(tmp_path) -> None:
    """teardown(env) also runs after a successful run()."""
    calls: list[str] = []

    class _TeardownSuite(_Suite):
        def prepare(self, target, dataset, driver):
            return EnvHandle({"run_id": "csbench-ok"})

        def run(self, target, env, driver):
            d = tmp_path / "raw"
            d.mkdir(exist_ok=True)
            (d / "r.json").write_text('{"resolved": 1, "total": 2}')
            return RawArtifacts(d, {"results": {"path": "r.json", "sha256": "x", "rows": None}})

        def teardown(self, env):
            calls.append(f"teardown:{env.payload.get('run_id', '')}")

    st = SuiteRunner(_TeardownSuite(), _Eval(), mock=False, artifacts_root=tmp_path / "store")
    st.execute(adapter=None, params={})
    assert calls == ["teardown:csbench-ok"]
