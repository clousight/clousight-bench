"""`csbench run-plan` writes a campaign manifest; `csbench progress` reads it.
The manifest never leaks into the record loaders (report/query)."""
import json

import yaml

from clousight_bench.cli import main
from clousight_bench.core.campaign import CAMPAIGNS_DIRNAME


def _plan(tmp_path):
    plan = {
        "version": "1",
        "domain": "agent-runtime",
        "platform": "local-sim",
        "target": {"startup": {"cold_ms": 50, "warm_ms": 5},
                   "recovery": {"mode": "auto-retry"}},
        "tasks": [{"task": "T1.1", "repeat": 1}, {"task": "T1.3", "repeat": 1}],
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
    assert "T1.1" in out and "T1.3" in out


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
    assert {t["task_id"] for t in data["tasks"]} == {"T1.1", "T1.3"}
    assert all(t["status"] == "completed" for t in data["tasks"])


def test_progress_with_no_campaign_is_a_clear_error(tmp_path, capsys):
    rc = main(["progress", "--results", str(tmp_path)])
    assert rc == 2
    assert "no campaigns" in capsys.readouterr().err


def test_manifest_does_not_pollute_the_report_loader(tmp_path, capsys):
    plan = _plan(tmp_path)
    results = tmp_path / "results"
    main(["run-plan", str(plan), "--results", str(results)])
    capsys.readouterr()

    from clousight_bench.core.report import _load_results
    records = _load_results(results)
    # Two tasks -> two terminal records; the manifest must not appear as one,
    # nor be reported as a skipped/broken record on stderr.
    assert len(records) == 2
    assert CAMPAIGNS_DIRNAME not in capsys.readouterr().err
