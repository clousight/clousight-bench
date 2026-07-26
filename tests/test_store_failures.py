"""Persistence must degrade, never disappear -- and never publish what it refused.

Every branch here answers the same question: after this failure, where is the
record, what does it still claim, and does the object we returned match the
bytes on disk?
"""
import hashlib
import json

import pytest

from clousight_bench.core.fingerprints import record_digest
from clousight_bench.core.persistence import EMERGENCY_DIR_NAME
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)
from clousight_bench.core.schema import utc_now
from clousight_bench.core.store import STORE_AVAILABLE, ResultStore


def _rec(**overrides) -> ResultRecord:
    base = dict(
        run=RunInfo(run_id="run-x", started_at=utc_now(), finished_at=utc_now()),
        identity=Identity(domain="agent-runtime", task_id="T1.3", task_revision="2",
                          scorer_revision="2", adapter="local-sim",
                          adapter_status="reference", core_version="0.2.0"),
        environment=Environment(region="", mode="local", python_version="3.12.0",
                                os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b",
                                  implementation="sha256:c"),
        status="completed",
        measurements={"p99_ms": {"value": 9, "unit": "ms", "evidence": "C"}},
    )
    base.update(overrides)
    return ResultRecord(**base)


def _written(path):
    return json.loads(path.read_text(encoding="utf-8"))


# --- I2: the returned record is the record on disk ---------------------------

def test_the_returned_record_equals_the_persisted_payload(tmp_path):
    record = _rec(series={"latency_ms": [[1, 10.0], [2, 20.0]]})
    path = ResultStore(tmp_path).persist(record)
    data = _written(path)

    assert record.to_dict() == data
    assert record_digest(data) == data["fingerprints"]["record_digest"]
    assert record.fingerprints.record_digest == data["fingerprints"]["record_digest"]


def test_the_series_pointer_is_visible_on_the_returned_record(tmp_path):
    if not STORE_AVAILABLE:
        pytest.skip("requires the [store] extra")
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)

    assert "$parquet" in record.series
    assert record.to_dict()["series"] == _written(path)["series"]


def test_records_are_identical_with_and_without_the_store_extra(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    with_extra = _rec(series={"latency_ms": [[1, 10.0]]})
    inline_path = ResultStore(tmp_path / "on").persist(with_extra)

    monkeypatch.setattr(store_mod, "STORE_AVAILABLE", False)
    without = _rec(series={"latency_ms": [[1, 10.0]]})
    plain_path = ResultStore(tmp_path / "off").persist(without)

    on, off = _written(inline_path), _written(plain_path)
    assert record_digest(on) == on["fingerprints"]["record_digest"]
    assert record_digest(off) == off["fingerprints"]["record_digest"]
    assert off["series"] == {"latency_ms": [[1, 10.0]]}
    for key in ("status", "measurements", "identity", "observations", "errors"):
        assert on[key] == off[key]
    assert on["run"]["stages"]["PERSIST"] == off["run"]["stages"]["PERSIST"] == "ok"


# --- I4: the sidecar is validated, hashed and atomic -------------------------

@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_the_series_sidecar_is_hashed_into_the_record(tmp_path):
    record = _rec(series={"latency_ms": [[1, 10.0], [2, 20.0]]})
    path = ResultStore(tmp_path).persist(record)
    pointer = _written(path)["series"]

    parquet = tmp_path / pointer["$parquet"]
    assert parquet.is_file()
    digest = "sha256:" + hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert pointer["sha256"] == digest
    assert pointer["rows"] == 2


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_a_tampered_sidecar_no_longer_matches_the_recorded_hash(tmp_path):
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)
    pointer = _written(path)["series"]
    parquet = tmp_path / pointer["$parquet"]

    parquet.write_bytes(parquet.read_bytes() + b"tampered")
    tampered = "sha256:" + hashlib.sha256(parquet.read_bytes()).hexdigest()
    assert tampered != pointer["sha256"]


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_no_orphan_sidecar_is_left_when_the_record_write_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))

    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    ResultStore(tmp_path / "results").persist(record)

    assert list((tmp_path / "results").rglob("*.parquet")) == []


def test_a_non_numeric_series_stays_inline_instead_of_failing(tmp_path):
    record = _rec(series={"phase": [[1, "warmup"]]})
    path = ResultStore(tmp_path).persist(record)
    data = _written(path)

    assert data["series"] == {"phase": [[1, "warmup"]]}
    assert data["run"]["stages"]["PERSIST"] == "ok"
    assert record.to_dict() == data


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_sidecar_leak_check_matches_inline_record_key_policy(tmp_path, monkeypatch):
    import clousight_bench.core.redaction as redaction

    monkeypatch.setattr(redaction, "identity_values", lambda: ("latency_ms",))
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)

    assert _written(path)["series"] == {"latency_ms": [[1, 10.0]]}
    assert not list(tmp_path.rglob("*.parquet"))


# --- C2 / I3: every render failure still produces a trustworthy record -------

def test_an_identity_leak_is_never_written_but_the_record_still_lands(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        "clousight_bench.core.store.find_identity_leaks",
        lambda payload: (
            ["$.environment.facts.host"] if payload.get("environment", {}).get("facts") else []
        ),
    )
    record = _rec(
        environment=Environment(region="", mode="local", python_version="3.12.0",
                                os_name="Linux", facts={"host": "build-box"}),
    )
    path = ResultStore(tmp_path).persist(record)
    data = _written(path)

    assert "build-box" not in json.dumps(data)
    assert "build-box" not in json.dumps(record.to_dict())
    assert data["run"]["stages"]["PERSIST"] == "failed"
    assert data["errors"][-1]["stage"] == "PERSIST"
    assert data["errors"][-1]["code"] == "identity_leak"
    assert record.to_dict() == data
    assert "identity" in capsys.readouterr().err


def test_a_non_canonical_payload_degrades_to_a_minimal_record(tmp_path, capsys):
    record = _rec(observations={"ratio": float("nan")})
    path = ResultStore(tmp_path).persist(record)
    text = path.read_text(encoding="utf-8")

    assert "NaN" not in text
    data = json.loads(text)
    assert data["observations"] == {}
    assert data["status"] == "failed"
    assert data["measurements"]["p99_ms"]["value"] == 9
    assert data["run"]["stages"]["PERSIST"] == "failed"
    assert data["errors"][-1]["stage"] == "PERSIST"
    assert data["extensions"]["core"]["persistence_degraded"] is True
    assert record_digest(data) == data["fingerprints"]["record_digest"]
    assert record.to_dict() == data
    assert capsys.readouterr().err


def test_drop_scored_level_is_failed_and_keeps_the_core_marker(tmp_path, capsys):
    record = _rec(
        measurements={
            "bad": {"value": object(), "unit": "", "evidence": "C"}
        }
    )
    path = ResultStore(tmp_path).persist(record)
    data = _written(path)

    assert data["status"] == "failed"
    assert data["measurements"] == {}
    assert data["extensions"] == {"core": {"persistence_degraded": True}}
    assert data["errors"][-1]["code"] == "persist_failed"
    assert data["run"]["stages"]["PERSIST"] == "failed"
    assert record.to_dict() == data
    assert capsys.readouterr().err


def test_fourth_level_minimum_is_always_canonical(tmp_path):
    class Hostile:
        def __str__(self):
            raise RuntimeError("no string")

    record = _rec(
        identity=Identity(
            domain=Hostile(),
            task_id="T1.3",
            task_revision="2",
            scorer_revision="2",
            adapter="local-sim",
            adapter_status="reference",
            core_version="0.2.0",
        ),
        measurements={"bad": {"value": Hostile(), "unit": "", "evidence": "C"}},
        errors=[{"message": Hostile()}],
    )
    payload = ResultStore(tmp_path)._degraded_payload(record)

    assert payload["schema_version"] == "0.2"
    assert payload["status"] == "failed"
    assert payload["run"]["stages"]["PERSIST"] == "failed"
    assert record_digest(payload) == payload["fingerprints"]["record_digest"]


def test_persist_never_reports_ok_before_the_bytes_are_on_disk(tmp_path, monkeypatch):
    seen = {}

    def _capture(path, text):
        seen["stages"] = json.loads(text)["run"]["stages"]
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _capture)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))
    record = _rec()
    path = ResultStore(tmp_path / "results").persist(record)

    assert seen["stages"]["PERSIST"] == "ok"  # the attempt claims what it intends
    assert _written(path)["run"]["stages"]["PERSIST"] == "failed"  # the record tells the truth
    assert record.run.stages["PERSIST"] == "failed"


def test_the_emergency_record_inlines_the_series_without_a_dangling_pointer(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))

    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path / "results").persist(record)
    data = _written(path)

    assert path.parent.name == EMERGENCY_DIR_NAME
    assert data["series"] == {"latency_ms": [[1, 10.0]]}
    assert record.series == {"latency_ms": [[1, 10.0]]}
    assert data["run"]["stages"]["PERSIST"] == "failed"
    err = capsys.readouterr().err
    assert str(path) in err
    assert "emergency" in err


def test_sidecar_oserror_keeps_inline_result_and_removes_empty_run_dir(
    tmp_path, monkeypatch
):
    import clousight_bench.core.store as store_mod

    run_dir = tmp_path / "agent-runtime" / "local-sim" / "run-x"

    def _boom(self, record):
        run_dir.mkdir(parents=True)
        raise OSError("sidecar disk full")

    monkeypatch.setattr(store_mod.ResultStore, "_build_series_sidecar", _boom)
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)

    assert _written(path)["series"] == {"latency_ms": [[1, 10.0]]}
    assert _written(path)["run"]["stages"]["PERSIST"] == "ok"
    assert not run_dir.exists()


@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_sidecar_write_oserror_retries_inline_in_normal_results(tmp_path, monkeypatch):
    def _boom(path, data):
        raise OSError("sidecar volume unavailable")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_bytes", _boom)
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)
    data = _written(path)

    assert path.parent == (tmp_path / "agent-runtime" / "local-sim").resolve()
    assert data["series"] == {"latency_ms": [[1, 10.0]]}
    assert data["run"]["stages"]["PERSIST"] == "ok"
    assert not (tmp_path / "agent-runtime" / "local-sim" / "run-x").exists()


def test_second_emergency_write_for_same_run_uses_a_unique_file(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))

    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    first = ResultStore(tmp_path / "results").persist(_rec())
    second = ResultStore(tmp_path / "results").persist(_rec())

    assert first != second
    assert first.is_file()
    assert second.is_file()
    assert _written(first)["status"] == "failed"
    assert _written(second)["status"] == "failed"


def test_a_parquet_failure_never_fails_the_run(tmp_path, monkeypatch):
    import clousight_bench.core.store as store_mod

    def _boom(self, record):
        raise RuntimeError("pyarrow exploded")

    monkeypatch.setattr(store_mod.ResultStore, "_build_series_sidecar", _boom)
    record = _rec(series={"latency_ms": [[1, 10.0]]})
    path = ResultStore(tmp_path).persist(record)

    assert _written(path)["series"] == {"latency_ms": [[1, 10.0]]}
    assert _written(path)["run"]["stages"]["PERSIST"] == "ok"


# --- I5: the query connection is closed, success or failure ------------------

@pytest.mark.skipif(not STORE_AVAILABLE, reason="requires [store] extra")
def test_query_series_closes_its_connection(tmp_path, monkeypatch):
    import duckdb

    import clousight_bench.core.store as store_mod

    store = store_mod.ResultStore(tmp_path)
    store.persist(_rec(series={"latency_ms": [[1, 10.0]]}))

    opened = []
    real_connect = duckdb.connect

    def _spy(*args, **kwargs):
        con = real_connect(*args, **kwargs)
        opened.append(con)
        return con

    monkeypatch.setattr(duckdb, "connect", _spy)
    store.query_series()
    assert len(opened) == 1
    with pytest.raises(Exception):
        opened[0].execute("SELECT 1")

    with pytest.raises(Exception):
        store.query_series("SELECT nope FROM series")
    assert len(opened) == 2
    with pytest.raises(Exception):
        opened[1].execute("SELECT 1")
