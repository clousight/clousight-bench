from clousight_bench.core.conformance import run_conformance


def test_builtin_agent_runtime_conforms():
    results = run_conformance("agent-runtime")
    failed = [r for r in results if not r.ok]
    assert not failed, failed


def test_platform_check_accepts_declared_adapter():
    results = run_conformance("agent-runtime", platform="local-sim")
    plat = [r for r in results if r.name.startswith("platform:")][0]
    assert plat.ok


def test_platform_check_rejects_unknown():
    results = run_conformance("agent-runtime", platform="nope")
    plat = [r for r in results if r.name.startswith("platform:")][0]
    assert plat.ok is False
