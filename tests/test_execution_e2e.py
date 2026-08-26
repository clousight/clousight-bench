from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_local_sim_run_is_simulated(tmp_path):
    rec = execute(RunSpec("agent-runtime", "stub.alt", "local-sim"), results_dir=tmp_path)
    assert rec.environment.execution == "simulated"
