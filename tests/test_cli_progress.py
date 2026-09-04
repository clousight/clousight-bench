"""`csbench run-plan` writes a campaign manifest; `csbench progress` reads it.
The manifest never leaks into the record loaders (query)."""

import json

import yaml

from clousight_bench.cli import main
from clousight_bench.core.campaign.manifest import CAMPAIGNS_DIRNAME


def _plan(tmp_path):
    plan = {
        "version": "1",
        "domain": "agent-runtime",
        "platform": "local-sim",
        "target": {"startup": {"cold_ms": 50, "warm_ms": 5}, "recovery": {"mode": "auto-retry"}},
        "tasks": [{"task": "suite:stub.alt", "repeat": 1}, {"task": "suite:stub.ok", "repeat": 1}],
    }
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump(plan))
    return p


def test_run_plan_writes_a_manifest_then_progress_reads_it(tmp_path, capsys):
    plan = _plan(tmp_path)
    results = tmp_path / "results"
    rc = main(["run-plan", str(plan), "--results", str(results)])
    assert rc == 0
    capsys.readouterr()

    manifests = list((results / CAMPAIGNS_DIRNAME).glob("*.json"))
    assert len(manifests) == 1

    rc = main(["progress", "--results", str(results)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "2/2 done" in out
    assert "suite:stub.alt" in out and "suite:stub.ok" in out


def test_progress_json_emits_the_manifest(tmp_path, capsys):
    plan = _plan(tmp_path)
    results = tmp_path / "results"
    main(["run-plan", str(plan), "--results", str(results)])
    capsys.readouterr()

    rc = main(["progress", "--results", str(results), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "campaign_manifest"
    assert data["total_tasks"] == 2
    assert {t["task_id"] for t in data["tasks"]} == {"suite:stub.alt", "suite:stub.ok"}
    assert all(t["status"] == "completed" for t in data["tasks"])


def test_progress_with_no_campaign_is_a_clear_error(tmp_path, capsys):
    rc = main(["progress", "--results", str(tmp_path)])
    assert rc == 2
    assert "no campaigns" in capsys.readouterr().err
