"""Tests for dev/prod mode wiring + prod subcommand registration."""

import pytest

from clousight_bench.cli import main


def test_run_plan_prod_mode_is_rejected(capsys):
    rc = main(["run-plan", "some-plan.yaml", "--mode", "prod"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "submit" in err  # points the user at the prod entrypoint


def test_prod_subcommands_are_registered():
    # --help exits 0 for a registered subcommand; a missing one would exit 2.
    for cmd in ("submit", "status", "logs", "fetch", "teardown"):
        with pytest.raises(SystemExit) as exc:
            main([cmd, "--help"])
        assert exc.value.code == 0
