from clousight_bench.cli import main


def test_conformance_command_passes_for_builtin(capsys):
    rc = main(["conformance", "--domain", "agent-runtime"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agent-runtime" in out
    assert "checks passed" in out


def test_conformance_with_platform(capsys):
    rc = main(["conformance", "--domain", "bigdata-emr", "--platform", "local-process"])
    assert rc == 0


def test_conformance_unknown_domain_exit_2(capsys):
    rc = main(["conformance", "--domain", "does-not-exist"])
    assert rc == 2
