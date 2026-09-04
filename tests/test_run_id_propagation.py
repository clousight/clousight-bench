"""The orchestrator hands the adapter this run's id before setup, so an adapter
can tag the resources it creates for later cost/billing reconciliation."""

from clousight_bench.core.orchestrator import execute
from clousight_bench.core.plugin import ProviderAdapter
from clousight_bench.core.schema import RunSpec


def test_adapter_run_id_defaults_to_none_outside_a_run():
    assert ProviderAdapter().run_id is None


def test_orchestrator_sets_run_id_on_the_adapter(tmp_path, monkeypatch):
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    seen: dict[str, str | None] = {}
    original = LocalSimAdapter.setup

    def spy(self: LocalSimAdapter) -> None:
        seen["run_id"] = self.run_id  # what the adapter sees at setup time
        original(self)

    monkeypatch.setattr(LocalSimAdapter, "setup", spy)
    rec = execute(
        RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
    )
    assert seen["run_id"] == rec.run.run_id
    assert seen["run_id"].startswith("run-")
