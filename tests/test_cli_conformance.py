from clousight_bench.cli import main


def test_conformance_command_passes_for_builtin(capsys):
    rc = main(["conformance", "--domain", "agent-runtime"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "agent-runtime" in out
    assert "checks passed" in out


def test_conformance_with_platform(capsys):
    rc = main(["conformance", "--domain", "agent-runtime", "--platform", "local-sim"])
    assert rc == 0


def test_conformance_unknown_domain_exit_2(capsys):
    rc = main(["conformance", "--domain", "does-not-exist"])
    assert rc == 2


# ---------------------------------------------------------------------------
# Task 7: suite-mode conformance
# ---------------------------------------------------------------------------


def test_conformance_cli_suite_pass(capsys):
    """conformance --suite swe-bench exits 0 and mentions evaluator:namespace + official-swe-evaluator."""
    rc = main(["conformance", "--suite", "swe-bench"])
    out = capsys.readouterr().out
    assert rc == 0, f"Expected exit 0, got {rc}; output:\n{out}"
    assert "evaluator:namespace" in out
    assert "official-swe-evaluator" in out


def test_conformance_cli_unknown_suite(capsys):
    """conformance --suite nope exits non-zero and lists available suites."""
    rc = main(["conformance", "--suite", "nope"])
    out, err = capsys.readouterr()
    assert rc != 0
    # The error message or output should mention swe-bench as available
    combined = out + err
    assert "swe-bench" in combined
