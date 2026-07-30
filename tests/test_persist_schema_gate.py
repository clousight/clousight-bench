"""PERSIST refuses to write a record that fails the 0.2 schema, dumping it raw."""
import tempfile
from pathlib import Path

import pytest

from clousight_bench.core.persistence import EMERGENCY_DIR_NAME
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.schema import utc_now
from clousight_bench.core.schema_validate import SchemaValidationError
from clousight_bench.core.store import ResultStore

pytest.importorskip("jsonschema")  # the gate only bites with full validation


def _rec() -> ResultRecord:
    return ResultRecord(
        run=RunInfo(run_id="run-x", started_at=utc_now(), finished_at=utc_now()),
        identity=Identity(domain="agent-runtime", task_id="T1.3", task_revision="2",
                          scorer_revision="2", adapter="local-sim",
                          adapter_status="reference", core_version="0.2.0"),
        environment=Environment(region="", mode="local", python_version="3.12.0",
                                os_name="Linux", facts={}),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b",
                                  implementation="sha256:c"),
        status="completed",
        measurements={"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
        series={},
    )


def test_valid_record_persists(tmp_path):
    path = ResultStore(tmp_path).persist(_rec())
    assert path.exists()


def test_invalid_status_is_rejected_and_dumped(tmp_path):
    emergency_dir = Path(tempfile.gettempdir()).resolve() / EMERGENCY_DIR_NAME
    before = set(emergency_dir.glob("INVALID-*run-x.json")) if emergency_dir.exists() else set()

    rec = _rec()
    rec.status = "bogus"  # not in the enum -> fails the schema
    try:
        with pytest.raises(SchemaValidationError):
            ResultStore(tmp_path).persist(rec)
        # the normal path was not written
        assert not (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").exists()
        # the raw record was emergency-dumped so nothing is lost
        after = set(emergency_dir.glob("INVALID-*run-x.json"))
        new = after - before
        assert new, "expected an emergency dump of the rejected record"
    finally:
        for p in set(emergency_dir.glob("INVALID-*run-x.json")) - before:
            p.unlink(missing_ok=True)
