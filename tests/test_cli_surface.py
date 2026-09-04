import re
import sys

import pytest

from clousight_bench.cli import _load_config, main
from clousight_bench.core.errors import UserInputError


def test_list_verbose_shows_task_and_adapter_status(capsys):
    rc = main(["list", "--verbose"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "suite:stub.ok" in out
    # Suite-first pivot: stub.ok is now the stub task; title check removed.
    assert "local-sim" in out and "reference" in out
    assert "aliyun-agentrun" in out and "experimental" in out
    assert "huawei-agentarts" in out and "skeleton" in out


@pytest.mark.real_registry
def test_list_shows_registered_suites_first(capsys):
    """Post-pivot, suites are what you run — list leads with them + a runnable hint."""
    rc = main(["list"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "benchmark suites:" in out
    assert "suite:swe-bench" in out
    assert "official-swe-evaluator (official)" in out
    # the hint must be copy-pasteable with the real benchmark id
    assert "csbench run --domain agent-runtime --benchmark swe-bench" in out
    # suites section comes BEFORE the domain section
    assert out.index("benchmark suites:") < out.index("domain: agent-runtime")


@pytest.mark.real_registry
def test_list_shows_platforms_and_no_task_lines(capsys):
    """Single rail: list shows each domain's platforms; task lines are gone."""
    rc = main(["list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "platforms :" in out
    assert "  tasks" not in out


def test_run_unknown_task_returns_usage_error_without_traceback(capsys):
    rc = main(["run", "--domain", "agent-runtime", "--task", "NOPE", "--platform", "local-sim"])
    captured = capsys.readouterr()

    assert rc == 2
    assert "NOPE" in captured.err
    assert "csbench list" in captured.err
    assert "Traceback" not in captured.err


def test_run_skeleton_returns_usage_error(capsys):
    # aliyun-agentrun is now provider-backed in the open core, so use a platform
    # that is still a pure skeleton (no registered provider) for this gate.
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "suite:stub.ok",
            "--platform",
            "huawei-agentarts",
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
            "suite:stub.ok",
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
            "suite:stub.ok",
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


def test_doctor_skeleton_warns_but_still_runs_preflight(capsys):
    """doctor is diagnostic, not execution: a skeleton adapter must not be a
    hard usage error here. It should print a clear warning that this platform
    is a skeleton (wiring / preflight requirements only, never a live check),
    then still call adapter.preflight(task) so the (benchmark x cloud) minimal
    permission list is visible before anyone wires the adapter.

    Uses huawei-agentarts: aliyun-agentrun is now experimental (its provider ran a
    live campaign), so it no longer prints the skeleton warning."""
    rc = main(
        [
            "doctor",
            "--domain",
            "agent-runtime",
            "--platform",
            "huawei-agentarts",
            "--task",
            "suite:stub.ok",
        ]
    )
    captured = capsys.readouterr()

    assert rc != 2
    assert not captured.err
    assert "skeleton" in captured.out
    assert "permissions" in captured.out


def test_doctor_skeleton_without_task_still_shows_wiring_warning(capsys):
    rc = main(
        [
            "doctor",
            "--domain",
            "agent-runtime",
            "--platform",
            "huawei-agentarts",
        ]
    )
    captured = capsys.readouterr()

    assert rc != 2
    assert "skeleton" in captured.out


def test_load_config_rejects_a_directory_path(tmp_path):
    directory = tmp_path / "not-a-file.yaml"
    directory.mkdir()

    with pytest.raises(UserInputError, match=re.escape(str(directory))):
        _load_config(str(directory))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_load_config_rejects_an_unreadable_file(tmp_path):
    config = tmp_path / "no-access.yaml"
    config.write_text("target: {}\n", encoding="utf-8")
    config.chmod(0o000)
    try:
        with pytest.raises(UserInputError, match=re.escape(str(config))):
            _load_config(str(config))
    finally:
        config.chmod(0o644)


def test_load_config_rejects_non_utf8_content(tmp_path):
    config = tmp_path / "bad-encoding.yaml"
    config.write_bytes("target:\n  region: \u4e2d\u56fd\n".encode("gb2312"))

    with pytest.raises(UserInputError, match=re.escape(str(config))):
        _load_config(str(config))


def test_run_rejects_a_directory_config_with_usage_error(tmp_path, capsys):
    directory = tmp_path / "cfg.yaml"
    directory.mkdir()

    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--task",
            "suite:stub.ok",
            "--platform",
            "local-sim",
            "--config",
            str(directory),
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert str(directory) in captured.err
    assert "Traceback" not in captured.err


def test_run_with_benchmark_flag_runs_mock(tmp_path, capsys):
    """R3: --benchmark <id> is the standard way to run a benchmark suite."""
    cfg = tmp_path / "mock.yaml"
    cfg.write_text("target:\n  mode: mock\n", encoding="utf-8")
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--benchmark",
            "swe-bench",
            "--platform",
            "local-sim",
            "--config",
            str(cfg),
            "--results",
            str(tmp_path / "r"),
            "--skip-preflight",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "completed"' in out
    assert "suite:swe-bench" in out  # canonical id preserved in the record


def test_run_without_task_or_benchmark_is_usage_error(tmp_path, capsys):
    rc = main(["run", "--domain", "agent-runtime", "--platform", "local-sim"])
    assert rc == 2
    assert "--benchmark" in capsys.readouterr().err


def test_run_benchmark_and_task_together_is_usage_error(capsys):
    rc = main(
        [
            "run",
            "--domain",
            "agent-runtime",
            "--benchmark",
            "swe-bench",
            "--task",
            "suite:stub.ok",
            "--platform",
            "local-sim",
        ]
    )
    assert rc == 2
    assert "not both" in capsys.readouterr().err
