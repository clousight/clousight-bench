"""A non-completed run must say WHY in one human paragraph, not only in JSON.

``csbench run`` prints the record to stdout for scripts. When the verdict is not
``completed`` the operator also needs the reason without grepping a screenful of
JSON -- so the CLI writes a short summary to stderr (stdout stays machine-clean).
"""

import json

from clousight_bench.cli import main
from clousight_bench.cli._common import run_summary
from clousight_bench.core.record import (
    Environment,
    Fingerprints,
    Identity,
    ResultRecord,
    RunInfo,
)


def _record(status: str, *, stages=None, errors=(), findings=()) -> ResultRecord:
    return ResultRecord(
        run=RunInfo(
            run_id="run-1",
            started_at="2026-09-05T00:00:00Z",
            finished_at="2026-09-05T00:00:01Z",
            stages=dict(stages or {"EXECUTE": "ok"}),
        ),
        identity=Identity(
            domain="key-value",
            task_id="suite:ycsb",
            task_revision="1",
            scorer_revision="1",
            adapter="ycsb-endpoint",
            adapter_status="reference",
            core_version="0.5.0",
        ),
        environment=Environment(region="", mode="local", python_version="3.12.0", os_name="Linux"),
        fingerprints=Fingerprints(benchmark="sha256:a", environment="sha256:b", implementation="sha256:c"),
        status=status,
        errors=list(errors),
        findings=list(findings),
    )


def test_a_completed_run_gets_no_summary():
    assert run_summary(_record("completed")) == ""


def test_the_summary_names_the_status_the_benchmark_and_the_platform():
    text = run_summary(_record("failed"))

    assert "failed" in text
    assert "suite:ycsb" in text
    assert "ycsb-endpoint" in text


def test_the_summary_names_the_failed_stage_and_its_message():
    text = run_summary(
        _record(
            "failed",
            stages={"SETUP": "ok", "EXECUTE": "failed"},
            errors=[
                {
                    "stage": "EXECUTE",
                    "code": "task_execution_failed",
                    "type": "RuntimeError",
                    "message": "the endpoint refused the connection",
                    "retryable": False,
                }
            ],
        )
    )

    assert "EXECUTE" in text
    assert "task_execution_failed" in text
    assert "the endpoint refused the connection" in text


def test_the_summary_surfaces_a_blocking_findings_remediation():
    """The live gate and the cost budget answer "why did SETUP not run?" in a
    finding's remediation -- the one line the operator actually needs."""
    text = run_summary(
        _record(
            "invalid",
            stages={"SETUP": "skipped"},
            findings=[
                {
                    "code": "live.unconfirmed",
                    "severity": "critical",
                    "summary": "live run not confirmed: real-cloud execution incurs cost",
                    "details": {"remediation": "re-run with --allow-live once you accept the cost"},
                }
            ],
        )
    )

    assert "live.unconfirmed" in text
    assert "live run not confirmed" in text
    assert "re-run with --allow-live" in text


def test_an_informational_finding_is_not_repeated_in_the_summary():
    text = run_summary(
        _record(
            "failed",
            findings=[{"code": "note.fyi", "severity": "info", "summary": "just so you know", "details": {}}],
        )
    )

    assert "note.fyi" not in text


def test_the_cli_prints_the_summary_to_stderr_and_keeps_stdout_json(tmp_path, monkeypatch, capsys):
    """End to end: a suite that blows up mid-run still leaves stdout parseable."""
    import clousight_bench.core.suite_runner as sr

    def _boom(self, adapter, params):  # noqa: ANN001, ARG001
        raise RuntimeError("the endpoint refused the connection")

    monkeypatch.setattr(sr.SuiteRunner, "execute", _boom)

    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--benchmark",
            "stub.ok",
            "--platform",
            "local-sim",
            "--results",
            str(tmp_path),
        ]
    )
    out, err = capsys.readouterr()

    assert rc != 0
    assert json.loads(out)["status"] == "failed"
    assert "EXECUTE" in err
    assert "the endpoint refused the connection" in err
    assert "Traceback" not in err
