"""WorkloadEngine resolves declared assets and exposes them to the workload."""

import hashlib
import json
import stat

import yaml

from clousight_bench.core.workload import WorkloadEngine

_RUNNER = """#!/usr/bin/env python3
import json, sys
params = json.load(open(sys.argv[sys.argv.index("--params") + 1]))
assets = params.get("assets", {})
# echo back that we received the resolved asset path as a metric-ish log
print(json.dumps({"type": "log", "message": "asset:" + ",".join(sorted(assets))}))
print(json.dumps({"type": "metric", "name": "asset_count", "value": len(assets)}))
print(json.dumps({"type": "result", "ok": True}))
"""


def _make_workload(tmp_path, blob):
    wl = tmp_path / "wl"
    (wl / "data").mkdir(parents=True)
    (wl / "data" / "corpus.bin").write_bytes(blob)
    run = wl / "run.py"
    run.write_text(_RUNNER)
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    manifest = {
        "name": "asset-wl",
        "version": "0.1.0",
        "entrypoint": "./run.py",
        "assets": [
            {
                "name": "corpus",
                "source": "bundled",
                "uri": "data/corpus.bin",
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        ],
    }
    (wl / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    return wl


def test_engine_resolves_bundled_asset_and_passes_path(tmp_path):
    wl = _make_workload(tmp_path, b"corpus-bytes")
    eng = WorkloadEngine(wl)

    resolved = eng.resolve_assets()
    assert "corpus" in resolved
    assert resolved["corpus"].endswith("data/corpus.bin")

    result = eng.run()
    assert result.ok
    assert result.metrics["asset_count"] == 1
    assert any("asset:corpus" in line for line in result.logs)


def test_describe_includes_asset_identity_not_contents(tmp_path):
    wl = _make_workload(tmp_path, b"corpus-bytes")
    desc = WorkloadEngine(wl).describe()
    assert desc["assets"][0]["name"] == "corpus"
    assert desc["assets"][0]["sha256"] == hashlib.sha256(b"corpus-bytes").hexdigest()
    # identity only -- no path / contents leaked into config_hash material
    assert "uri" not in desc["assets"][0]
    assert "path" not in json.dumps(desc["assets"][0])
