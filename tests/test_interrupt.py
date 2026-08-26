"""An interrupt (Ctrl-C / SIGTERM) mid-run still tears down and persists an
interrupted record, so resources are released and progress is not lost."""

import glob
import json
import threading

import pytest

from clousight_bench.core import orchestrator
from clousight_bench.core.orchestrator import _terminate_as_interrupt, execute
from clousight_bench.core.schema import RunSpec


def _spec():
    return RunSpec("agent-runtime", "stub.ok", "local-sim", target={"recovery": {"mode": "auto-retry"}})


def test_interrupt_runs_teardown_and_persists_interrupted_record(tmp_path, monkeypatch):
    from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

    torn = {"ran": False}
    original_teardown = LocalSimAdapter.teardown

    def spy_teardown(self: LocalSimAdapter) -> None:
        torn["ran"] = True
        original_teardown(self)

    monkeypatch.setattr(LocalSimAdapter, "teardown", spy_teardown)

    # Interrupt COLLECT (setup + execute already succeeded, so teardown is owed).
    def boom(_bundle):
        raise KeyboardInterrupt("sigint")

    monkeypatch.setattr(orchestrator, "collect", boom)

    with pytest.raises(KeyboardInterrupt):
        execute(_spec(), results_dir=tmp_path)

    assert torn["ran"] is True, "teardown must run on interrupt"

    files = glob.glob(str(tmp_path / "**" / "*.json"), recursive=True)
    records = [json.loads(open(f, encoding="utf-8").read()) for f in files]
    interrupted = [r for r in records if r.get("status") == "interrupted"]
    assert interrupted, "an interrupted record must be persisted"
    rec = interrupted[0]
    assert rec["run"]["stages"].get("TEARDOWN") == "ok"
    assert rec["run"]["stages"].get("SETUP") == "ok"


def test_terminate_as_interrupt_is_a_noop_off_the_main_thread():
    ran = {"ok": False}

    def worker() -> None:
        with _terminate_as_interrupt():  # must not raise off the main thread
            ran["ok"] = True

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert ran["ok"] is True
