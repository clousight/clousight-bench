import json

from clousight_bench.cli import main
from clousight_bench.core.orchestrator import execute
from clousight_bench.core.schema import RunSpec


def test_dump_bundle_writes_json(tmp_path):
    execute(RunSpec("agent-runtime", "T1.1", "local-sim"), results_dir=tmp_path)
    out = tmp_path / "b.json"
    rc = main(["report", "--results", str(tmp_path), "--dump-bundle", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "report-bundle/1.0"
    assert data["domains"][0]["domain"] == "agent-runtime"
