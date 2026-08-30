"""`csbench run --repeat/--warmup` runs a plan and prints an aggregate; a plain
run is byte-for-byte unchanged."""

import json

from clousight_bench.cli import main
from clousight_bench.core.runplan import AGGREGATES_DIRNAME


def _run(argv):
    return main(argv)


def test_a_plain_run_still_prints_one_record(tmp_path, capsys):
    rc = _run(
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
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["schema_version"] == "0.4"
    assert "run_plan" not in out.get("extensions", {}).get("core", {})


def test_repeat_prints_an_aggregate_and_persists_it(tmp_path, capsys):
    rc = _run(
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
            "--repeat",
            "3",
            "--warmup",
            "1",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["kind"] == "run_plan_aggregate"
    assert len(out["runs"]["measured"]) == 3
    assert len(out["runs"]["warmup"]) == 1
    # Suite-first pivot: stub task emits "ok" measurement, not "observed_attempts".
    assert out["measurements"]["ok"]["n"] == 3
    assert list((tmp_path / AGGREGATES_DIRNAME).rglob("*.json"))


def test_a_bad_repeat_is_a_user_input_error(tmp_path, capsys):
    rc = _run(
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
            "--repeat",
            "0",
        ]
    )
    assert rc == 2
    assert "repeat" in capsys.readouterr().err
