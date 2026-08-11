from clousight_bench.core.conformance import _check_task, run_conformance


def test_builtin_agent_runtime_conforms():
    results = run_conformance("agent-runtime")
    failed = [r for r in results if not r.ok]
    assert not failed, failed


def test_builtin_bigdata_emr_conforms():
    results = run_conformance("bigdata-emr")
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


def test_bad_evidence_layer_flagged():
    class _BadTask:
        task_id = "T0"
        title = "x"
        evidence_layer = "Z"
        task_revision = "1"
        scorer_revision = "1"

        def config(self, p):
            return {}

        def execute(self, a, p): ...

        def score(self, o): ...

    results = _check_task("T0", _BadTask)
    ev = [r for r in results if r.name.endswith(":evidence")][0]
    assert ev.ok is False


def test_missing_revision_flagged():
    class _NoRev:
        task_id = "T0"
        title = "x"
        evidence_layer = "C"
        task_revision = ""
        scorer_revision = ""

        def config(self, p):
            return {}

        def execute(self, a, p): ...

        def score(self, o): ...

    results = _check_task("T0", _NoRev)
    rev = [r for r in results if r.name.endswith(":revisions")][0]
    assert rev.ok is False
