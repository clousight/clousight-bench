"""P0-2: run-id resource tagging + orphan-sweep reconciliation.

Every cloud resource a wired adapter creates must carry the run's id so an
interrupted / SIGKILLed run's orphans (which keep billing) can be found and
reaped. Open-core owns the tag convention + the sweep seam; the actual cloud
reaper is a plugin the pro pack installs.
"""

from clousight_bench.cli import main
from clousight_bench.core import registry
from clousight_bench.core.plugin import ResourceReaper
from clousight_bench.core.resource_tags import TAG_MANAGED, TAG_RUN_ID, run_tags
from clousight_bench.domains.agent_runtime.adapters.local_sim import LocalSimAdapter

# --- the tag convention -----------------------------------------------------

def test_run_tags_carry_run_id_and_managed_marker():
    tags = run_tags("run-abc123")
    assert tags[TAG_RUN_ID] == "run-abc123"
    assert tags[TAG_MANAGED] == "true"


def test_run_tags_merge_caller_extra():
    tags = run_tags("run-abc123", {"team": "platform"})
    assert tags["team"] == "platform"
    assert tags[TAG_RUN_ID] == "run-abc123"


def test_run_tags_without_run_id_still_marks_managed():
    tags = run_tags(None)
    assert tags[TAG_MANAGED] == "true"
    assert TAG_RUN_ID not in tags


def test_adapter_resource_tags_use_its_run_id():
    a = LocalSimAdapter({"resource_tags": {"env": "ci"}})
    a.run_id = "run-xyz"
    tags = a.resource_tags()
    assert tags[TAG_RUN_ID] == "run-xyz"
    assert tags["env"] == "ci"


# --- the sweep seam ---------------------------------------------------------

def test_sweep_without_a_reaper_fails_clearly(capsys, monkeypatch):
    monkeypatch.setattr(registry, "get_resource_reaper", lambda provider: None)
    code = main(["sweep", "--provider", "aliyun"])
    err = capsys.readouterr().err
    assert code != 0
    assert "aliyun" in err
    assert "reaper" in err.lower()


def test_sweep_defaults_to_dry_run_and_calls_the_reaper(capsys, monkeypatch):
    calls: list[dict] = []

    class _FakeReaper(ResourceReaper):
        provider = "aliyun"

        def sweep(self, *, dry_run, older_than_s=None):
            calls.append({"dry_run": dry_run, "older_than_s": older_than_s})
            return [{"id": "runtime-1", "run_id": "run-old"}]

    monkeypatch.setattr(registry, "get_resource_reaper",
                        lambda provider: _FakeReaper() if provider == "aliyun" else None)
    code = main(["sweep", "--provider", "aliyun"])
    out = capsys.readouterr().out
    assert code == 0
    assert calls == [{"dry_run": True, "older_than_s": None}]
    assert "runtime-1" in out


def test_sweep_confirm_actually_deletes(capsys, monkeypatch):
    calls: list[bool] = []

    class _FakeReaper(ResourceReaper):
        provider = "aliyun"

        def sweep(self, *, dry_run, older_than_s=None):
            calls.append(dry_run)
            return []

    monkeypatch.setattr(registry, "get_resource_reaper",
                        lambda provider: _FakeReaper())
    code = main(["sweep", "--provider", "aliyun", "--confirm"])
    assert code == 0
    assert calls == [False]  # --confirm turns off dry-run
