import re
import sys

import pytest

from clousight_bench.cli import _load_config, main
from clousight_bench.core.errors import UserInputError


def test_list_verbose_shows_task_and_adapter_status(capsys):
    rc = main(["list", "--verbose"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "T1.3" in out
    assert "Tool-failure recovery" in out
    assert "local-sim" in out and "reference" in out
    assert "aliyun-agentrun" in out and "skeleton" in out


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
            "T1.3",
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


def test_doctor_skeleton_warns_but_still_runs_preflight(capsys):
    """doctor is diagnostic, not execution: a skeleton adapter must not be a
    hard usage error here. It should print a clear warning that this platform
    is a skeleton (wiring / preflight requirements only, never a live check),
    then still call adapter.preflight(task) so the (benchmark x cloud) minimal
    permission list is visible before anyone wires the adapter."""
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
            "aliyun-agentrun",
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
            "T1.3",
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
