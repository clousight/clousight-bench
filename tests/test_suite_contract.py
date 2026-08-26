from pathlib import Path

from clousight_bench.core.suite import (
    BenchmarkSuite,
    DatasetHandle,
    DriverContext,
    EnvHandle,
    Evaluator,
    RawArtifacts,
    Target,
)


def test_dataset_handle_exposes_only_version_and_digest():
    d = DatasetHandle(version="hf@abc", digest="sha256:1", payload={"instances": [1, 2]})
    assert d.version == "hf@abc" and d.digest == "sha256:1"


def test_raw_artifacts_path_resolves_from_manifest(tmp_path):
    (tmp_path / "results.json").write_text("{}")
    ra = RawArtifacts(
        dir=tmp_path,
        manifest={"results": {"path": "results.json", "sha256": "x", "rows": None}},
    )
    assert ra.path("results") == tmp_path / "results.json"


def test_target_and_driver_context_enums():
    t = Target(mode="endpoint", mock=True, handle=None)
    dc = DriverContext(placement="local")
    assert t.mode == "endpoint" and t.mock is True and dc.placement == "local"


def test_a_minimal_suite_and_evaluator_satisfy_the_abcs():
    class S(BenchmarkSuite):
        suite_id = "demo"
        suite_version = "v0"

        def resolve(self, cfg, assets):
            return DatasetHandle("v0", "sha256:d", {})

        def prepare(self, target, dataset, driver):
            return EnvHandle({})

        def run(self, target, env, driver):
            return RawArtifacts(Path("."), {})

        def mock_artifacts(self, cfg):
            return RawArtifacts(Path("."), {})

    class E(Evaluator):
        evaluator_id = "demo-eval"
        official = True

        def supports(self, suite_id, product):
            return suite_id == "demo"

        def evaluate(self, raw):
            return {}

    assert S().suite_id == "demo" and E().supports("demo", "x") is True
    S().teardown(EnvHandle({}))  # default no-op
