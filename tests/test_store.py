"""ResultStore: atomic 0.2 records, optional Parquet series, emergency fallback."""

import json

import pytest

from clousight_bench.core.persistence import EMERGENCY_DIR_NAME
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.redaction import SensitiveDataError
from clousight_bench.core.schema import utc_now
from clousight_bench.core.store import STORE_AVAILABLE, ResultStore


def _rec(series=None, measurements=None, facts=None) -> ResultRecord:
    return ResultRecord(
        run=RunInfo(run_id="run-x", started_at=utc_now(), finished_at=utc_now()),
        identity=Identity(
            domain="agent-runtime",
            task_id="T1.3",
            task_revision="2",
            scorer_revision="2",
            adapter="local-sim",
            adapter_status="reference",
            core_version="0.2.0",
        ),
        environment=Environment(
            region="", mode="local", python_version="3.12.0", os_name="Linux", facts=facts or {}
        ),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b", implementation="sha256:c"),
        status="completed",
        measurements=measurements or {"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
        series=series or {},
    )


def test_persist_keeps_the_domain_adapter_task_run_layout(tmp_path):
    path = ResultStore(tmp_path).persist(_rec())
    expected = (tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").resolve()
    assert path == expected
    data = json.loads(expected.read_text(encoding="utf-8"))
    assert data["schema_version"] == "0.2"
    assert data["measurements"]["p99_ms"]["value"] == 9
    assert data["run"]["stages"]["PERSIST"] == "ok"


def test_persist_stamps_a_record_digest(tmp_path):
    record = _rec()
    path = ResultStore(tmp_path).persist(record)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["fingerprints"]["record_digest"].startswith("sha256:")
    assert record.fingerprints.record_digest == data["fingerprints"]["record_digest"]


def test_persist_leaves_no_temp_file(tmp_path):
    ResultStore(tmp_path).persist(_rec())
    names = sorted(p.name for p in (tmp_path / "agent-runtime" / "local-sim").iterdir())
    assert names == ["T1.3-run-x.json"]


def test_persist_refuses_to_write_an_operator_identity(tmp_path, monkeypatch):
    """Refusing to publish the leak must not mean refusing to keep the result."""
    monkeypatch.setattr(
        "clousight_bench.core.store.find_identity_leaks",
        lambda payload: ["$.environment.facts.host"] if payload["environment"]["facts"] else [],
    )
    record = _rec(facts={"host": "build-box"})
    path = ResultStore(tmp_path).persist(record)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert "build-box" not in json.dumps(data)
    assert data["environment"]["facts"] == {}
    assert data["errors"][-1]["code"] == "identity_leak"
    assert data["errors"][-1]["type"] == SensitiveDataError.__name__
    assert data["run"]["stages"]["PERSIST"] == "failed"


def test_primary_write_failure_falls_back_to_an_emergency_file(tmp_path, monkeypatch, capsys):
    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    record = _rec()
    path = ResultStore(tmp_path / "results").persist(record)

    assert path.is_absolute()
    assert path.parent.name == EMERGENCY_DIR_NAME
    assert str(path) in capsys.readouterr().err
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run"]["stages"]["PERSIST"] == "failed"
    assert data["errors"][-1]["stage"] == "PERSIST"
    assert data["errors"][-1]["type"] == "OSError"


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_series_externalized_to_parquet_and_queryable(tmp_path):
    store = ResultStore(tmp_path)
    store.persist(
        _rec(
            series={"latency_ms": [[1, 10.0], [2, 20.0]]},
            measurements={"latency_ms": {"value": 15, "unit": "ms", "evidence": "C"}},
        )
    )
    parquet = tmp_path / "agent-runtime" / "local-sim" / "run-x" / "series.parquet"
    assert parquet.exists()
    record_json = json.loads((tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text())
    assert record_json["series"]["$parquet"] == ("agent-runtime/local-sim/run-x/series.parquet")
    assert record_json["series"]["rows"] == 2
    assert record_json["series"]["sha256"].startswith("sha256:")
    rows = store.query_series("SELECT series, unit, count(*) AS n FROM series GROUP BY series, unit")
    assert rows == [{"series": "latency_ms", "unit": "ms", "n": 2}]


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_an_unwritable_parquet_sidecar_keeps_the_series_inline(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    def _boom(self, record):
        raise OSError("no space left on device")

    monkeypatch.setattr(store_mod.ResultStore, "_build_series_sidecar", _boom)
    path = ResultStore(tmp_path).persist(_rec(series={"latency_ms": [[1, 10.0]]}))

    assert json.loads(path.read_text(encoding="utf-8"))["series"] == {"latency_ms": [[1, 10.0]]}


def test_series_inline_when_store_unavailable(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    monkeypatch.setattr(store_mod, "STORE_AVAILABLE", False)
    store_mod.ResultStore(tmp_path).persist(_rec(series={"latency_ms": [[1, 10.0]]}))
    record_json = json.loads((tmp_path / "agent-runtime" / "local-sim" / "T1.3-run-x.json").read_text())
    assert record_json["series"] == {"latency_ms": [[1, 10.0]]}


def test_validate_sidecar_accepts_flat_fetch_layout(tmp_path):
    import hashlib

    import pyarrow as pa
    import pyarrow.parquet as pq

    from clousight_bench.core.store import validate_sidecar

    tbl = pa.table({"series": ["m"], "t": [1], "value": [1.0], "unit": [""]})
    flat = tmp_path / "T9.9.series.parquet"
    pq.write_table(tbl, flat)
    data = flat.read_bytes()
    payload = {
        "identity": {"task_id": "T9.9"},
        "series": {
            "$parquet": "agent-runtime/x/run-1/series.parquet",
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "rows": 1,
        },
    }
    path, err = validate_sidecar(tmp_path, payload)
    assert err is None
    assert path == flat.resolve()


def test_validate_sidecar_flat_still_checks_sha(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from clousight_bench.core.store import validate_sidecar

    tbl = pa.table({"series": ["m"], "t": [1], "value": [1.0], "unit": [""]})
    pq.write_table(tbl, tmp_path / "T9.9.series.parquet")
    payload = {
        "identity": {"task_id": "T9.9"},
        "series": {"$parquet": "nope/series.parquet", "sha256": "sha256:deadbeef", "rows": 1},
    }
    _, err = validate_sidecar(tmp_path, payload)
    assert err == "sidecar sha256 mismatch"
