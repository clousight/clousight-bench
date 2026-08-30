"""The request side of a run, plus the version contract."""

from clousight_bench.core.schema import RunSpec


def test_runspec_to_dict():
    spec = RunSpec(domain="d", task_id="t", platform="p", target={"k": "v"}, params={"n": 1})
    assert spec.to_dict()["target"] == {"k": "v"}


def test_plugin_api_version_exposed():
    import clousight_bench

    assert clousight_bench.PLUGIN_API_VERSION == "1.0"


def test_package_and_schema_versions():
    import clousight_bench

    assert clousight_bench.__version__ == "0.5.0"
    assert clousight_bench.RUNNER_VERSION == "0.5.0"
    assert clousight_bench.RESULT_SCHEMA_VERSION == "0.4"  # bumped for per-item substrate (R1)


def test_result_record_is_reexported_from_schema():
    from clousight_bench.core.record import ResultRecord as Defined
    from clousight_bench.core.schema import ResultRecord as Reexported

    assert Reexported is Defined
