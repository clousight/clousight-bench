import pytest

from clousight_bench.core.schema_validate import (
    SchemaValidationError,
    load_schema,
    validate_against_schema,
)

jsonschema = pytest.importorskip("jsonschema")


def test_load_schema_reads_packaged_file():
    schema = load_schema("runspec")
    assert schema["title"].startswith("Clousight Bench")


def test_runspec_good_passes():
    validate_against_schema(
        {"domain": "d", "task_id": "t", "platform": "p", "target": {}, "params": {}},
        "runspec",
    )


def test_runspec_missing_field_rejected():
    with pytest.raises(SchemaValidationError):
        validate_against_schema({"domain": "d"}, "runspec")


def test_result_record_bad_status_rejected():
    bad = {"schema_version": "0.2", "status": "bogus"}
    with pytest.raises(SchemaValidationError):
        validate_against_schema(bad, "result-record-0.2")


def test_manifest_good_passes():
    validate_against_schema(
        {"name": "w", "version": "0.1.0", "entrypoint": "./run.sh"},
        "workload-manifest",
    )
