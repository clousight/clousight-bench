"""WorkloadEngine protocol: sample + artifact events parse into WorkloadResult."""
import hashlib
import stat
from pathlib import Path

from clousight_bench.core.workload import WorkloadEngine


def _make_workload(tmp_path: Path, script: str) -> Path:
    (tmp_path / "manifest.yaml").write_text(
        "name: proto-test\nversion: 0.0.1\nentrypoint: ./run.sh\n", encoding="utf-8"
    )
    run = tmp_path / "run.sh"
    run.write_text(script, encoding="utf-8")
    run.chmod(run.stat().st_mode | stat.S_IEXEC)
    return tmp_path


def test_sample_events_accumulate_into_series(tmp_path):
    script = (
        "#!/usr/bin/env bash\n"
        'echo \'{"type":"sample","series":"latency_ms","t":1,"value":10}\'\n'
        'echo \'{"type":"sample","series":"latency_ms","t":2,"value":20}\'\n'
        'echo \'{"type":"result","ok":true}\'\n'
    )
    wl = WorkloadEngine(_make_workload(tmp_path, script))
    res = wl.run()
    assert res.ok
    assert res.series["latency_ms"] == [[1, 10], [2, 20]]


def test_artifact_event_gets_sha256(tmp_path):
    (tmp_path / "trace.json").write_text('{"span":1}', encoding="utf-8")
    expected = "sha256:" + hashlib.sha256(b'{"span":1}').hexdigest()
    script = (
        "#!/usr/bin/env bash\n"
        'echo \'{"type":"artifact","kind":"otel_trace","path":"trace.json","media":"application/json"}\'\n'
        'echo \'{"type":"result","ok":true}\'\n'
    )
    wl = WorkloadEngine(_make_workload(tmp_path, script))
    res = wl.run()
    assert res.ok
    assert len(res.artifacts) == 1
    art = res.artifacts[0]
    assert art["kind"] == "otel_trace"
    assert art["path"] == "trace.json"
    assert art["media"] == "application/json"
    assert art["sha256"] == expected
