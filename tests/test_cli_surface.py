import pytest

from clousight_bench.cli import main


def test_list_verbose_shows_task_and_adapter_status(capsys):
    rc = main(["list", "--verbose"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "T1.3" in out
    assert "Tool-failure recovery" in out
    assert "local-sim" in out and "reference" in out
    assert "aliyun-agentrun" in out and "skeleton" in out


def test_run_unknown_task_returns_usage_error_without_traceback(capsys):
    rc = main(
        ["run", "--domain", "agent-runtime", "--task", "NOPE", "--platform", "local-sim"]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "NOPE" in captured.err
    assert "csbench list" in captured.err
    assert "Traceback" not in captured.err


def test_run_skeleton_returns_usage_error(capsys):
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "T1.3",
            "--platform",
            "aliyun-agentrun",
            "--skip-preflight",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "skeleton" in captured.err


def test_run_missing_config_returns_usage_error(capsys):
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "T1.3",
            "--platform",
            "local-sim",
            "--config",
            "does-not-exist.yaml",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "does-not-exist.yaml" in captured.err


@pytest.mark.parametrize("content", ["[]", "false"])
def test_run_rejects_non_mapping_config_roots(tmp_path, capsys, content):
    config = tmp_path / "invalid-root.yaml"
    config.write_text(content, encoding="utf-8")

    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "T1.3",
            "--platform",
            "local-sim",
            "--config",
            str(config),
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "config root must be a mapping" in captured.err
    assert "csbench list" in captured.err


def test_doctor_unknown_task_returns_usage_error(capsys):
    rc = main(
        [
            "doctor",
            "--domain",
            "agent-runtime",
            "--platform",
            "local-sim",
            "--task",
            "NOPE",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "NOPE" in captured.err
    assert "csbench list" in captured.err


def test_doctor_unknown_platform_returns_usage_error(capsys):
    rc = main(
        [
            "doctor",
            "--domain",
            "agent-runtime",
            "--platform",
            "nope",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "nope" in captured.err
    assert "csbench list" in captured.err


def test_doctor_skeleton_returns_usage_error(capsys):
    rc = main(
        [
            "doctor",
            "--domain",
            "agent-runtime",
            "--platform",
            "aliyun-agentrun",
            "--task",
            "T1.3",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert "skeleton" in captured.err
    assert "csbench list" in captured.err
