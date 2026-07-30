"""A hung stage hits the deadline and is recorded as a failure -- and teardown
still runs, so a stuck run cannot block a pipeline or orphan resources."""
import signal
import time

import pytest

from clousight_bench.core import orchestrator
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec

_HAS_ALARM = hasattr(signal, "SIGALRM")


def _spec():
    return RunSpec("agent-runtime", "T1.3", "local-sim",
                   target={"recovery": {"mode": "auto-retry"}})


@pytest.mark.skipif(not _HAS_ALARM, reason="stage deadline needs SIGALRM")
def test_hung_stage_hits_deadline_and_still_tears_down(tmp_path, monkeypatch):
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    torn = {"ran": False}
    original_teardown = LocalSimAdapter.teardown

    def spy_teardown(self: LocalSimAdapter) -> None:
        torn["ran"] = True
        original_teardown(self)

    monkeypatch.setattr(LocalSimAdapter, "teardown", spy_teardown)

    def slow_collect(bundle):
        time.sleep(3)  # longer than the deadline; SIGALRM interrupts it
        return bundle

    monkeypatch.setattr(orchestrator, "collect", slow_collect)

    rec = execute(_spec(), results_dir=tmp_path, timeout_s=0.2)

    assert rec.status == "failed"
    assert torn["ran"] is True, "teardown must run even when a stage times out"
    assert any(
        e.get("type") == "TimeoutError" or "deadline" in str(e.get("message", ""))
        for e in rec.errors
    )


def test_no_timeout_completes_normally(tmp_path):
    rec = execute(_spec(), results_dir=tmp_path, timeout_s=None)
    assert rec.status == "completed"
