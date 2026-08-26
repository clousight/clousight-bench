"""The CLI's contract with a script: exit codes, stderr, and what stdout claims."""

import json

from clousight_bench.cli import main
from clousight_bench.core.fingerprints import record_digest


def _run(tmp_path, *extra):
    return main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "stub.ok",
            "--platform",
            "local-sim",
            "--results",
            str(tmp_path),
            *extra,
        ]
    )


# --- I7: every bad invocation is exit 2 with a usable message ----------------


def test_a_malformed_param_is_a_usage_error(tmp_path, capsys):
    rc = _run(tmp_path, "--param", "oops")
    err = capsys.readouterr().err

    assert rc == 2
    assert "key=value" in err
    assert "Traceback" not in err


def test_init_with_an_unknown_provider_is_a_usage_error(tmp_path, capsys):
    rc = main(["init", "nosuchcloud", "--out", str(tmp_path)])
    err = capsys.readouterr().err

    assert rc == 2
    assert "nosuchcloud" in err
    assert "Traceback" not in err


# --- I2: stdout is the record that was persisted -----------------------------


def test_stdout_matches_the_persisted_record_byte_for_byte_in_meaning(tmp_path, capsys):
    rc = _run(tmp_path)
    printed = json.loads(capsys.readouterr().out)

    assert rc == 0
    files = list((tmp_path / "agent-runtime" / "local-sim").glob("stub.ok-*.json"))
    assert len(files) == 1
    on_disk = json.loads(files[0].read_text(encoding="utf-8"))

    assert printed == on_disk
    assert record_digest(printed) == printed["fingerprints"]["record_digest"]


# --- M6: an emergency write is never reported as a normal result -------------


def test_a_failed_persist_exits_non_zero_and_says_where_the_record_went(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "tmp"))

    def _boom(path, text):
        raise OSError("read-only file system")

    monkeypatch.setattr("clousight_bench.core.store.atomic_write_text", _boom)
    rc = _run(tmp_path / "results")
    captured = capsys.readouterr()

    assert rc == 1
    assert "emergency" in captured.err
    printed = json.loads(captured.out)
    assert printed["status"] == "failed"
    assert printed["run"]["stages"]["PERSIST"] == "failed"
    assert printed["errors"][-1]["stage"] == "PERSIST"
    assert printed["extensions"]["core"]["persistence_degraded"] is True


def test_stdout_verifies_even_when_the_series_moved_to_parquet(tmp_path, capsys):
    """A printed record must be verifiable, pointer and all."""
    import clousight_bench.core.orchestrator as orch
    from clousight_bench.core.observation import (
        Measurement,
        ObservationBundle,
        TaskResult,
    )
    from clousight_bench.core.plugin import DomainPack, ProviderAdapter, Task
    from clousight_bench.core.store import STORE_AVAILABLE

    if not STORE_AVAILABLE:
        return

    class _Adapter(ProviderAdapter):
        name = "fake"
        status = "reference"

    class _Task(Task):
        task_id = "TS"

        def config(self, params):
            return {}

        def execute(self, adapter, params):
            return ObservationBundle(series={"latency_ms": [[1, 10.0], [2, 12.5]]})

        def score(self, observations):
            return TaskResult(
                measurements={
                    "latency_ms": Measurement(value=12.5, unit="ms", reproducibility_class="environmental")
                }
            )

    class _Domain(DomainPack):
        domain = "fake-domain"

        def tasks(self):
            return {"TS": _Task}

        def adapters(self):
            return {"fake": _Adapter}

    original = orch.get_domain
    orch.get_domain = lambda name: _Domain()
    try:
        rc = main(
            [
                "run",
                "--domain",
                "fake-domain",
                "--task",
                "TS",
                "--platform",
                "fake",
                "--results",
                str(tmp_path),
                "--no-enrich",
            ]
        )
    finally:
        orch.get_domain = original

    printed = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert printed["series"]["rows"] == 2
    assert (tmp_path / printed["series"]["$parquet"]).is_file()
    assert record_digest(printed) == printed["fingerprints"]["record_digest"]

    on_disk = json.loads(
        (tmp_path / "fake-domain" / "fake" / "TS-{}.json".format(printed["run"]["run_id"])).read_text(
            encoding="utf-8"
        )
    )
    assert printed == on_disk


def test_debug_writes_only_a_local_log_and_never_touches_the_record(tmp_path, capsys):
    rc = _run(tmp_path, "--debug")
    printed = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert "Traceback" not in json.dumps(printed)
    # a clean run has nothing to log
    assert not (tmp_path / "debug").exists()
