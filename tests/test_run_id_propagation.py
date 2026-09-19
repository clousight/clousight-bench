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


def test_a_caller_may_name_the_run_before_it_starts(tmp_path):
    """The console has to know the run's id before the run exists.

    `POST /api/runs` answers with an id and the browser immediately opens the
    live view on it, so the id cannot be invented by the subprocess minutes
    later — the reply would name a run nothing had heard of.
    """
    rec = execute(
        RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}}),
        results_dir=tmp_path,
        run_id="run-20260919-120000-abcdef",
    )
    assert rec.run.run_id == "run-20260919-120000-abcdef"
    # The progress plane is keyed on it too, which is the whole point.
    assert (tmp_path / ".progress" / "run-20260919-120000-abcdef" / "state.json").exists()


def test_a_supplied_run_id_must_be_a_plain_token(tmp_path):
    """It names a directory, so it is checked before it can name one."""
    import pytest

    from clousight_bench.core.errors import UserInputError

    with pytest.raises(UserInputError):
        execute(
            RunSpec("agent-runtime", "suite:stub.ok", "local-sim", target={}),
            results_dir=tmp_path,
            run_id="../escape",
        )
