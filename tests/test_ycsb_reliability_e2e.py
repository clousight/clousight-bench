"""R5 end-to-end: a fake YCSB launcher drives a REAL redis through the harness's
disruption proxy — the run completes, the disruption is recorded as evidence."""

from __future__ import annotations

import json
import shutil
import socket
import stat
import subprocess
import time

import pytest

from clousight_bench.core.suite import DriverContext, Target
from clousight_bench.suites.ycsb.suite import YcsbSuite

_FAKE_YCSB = '''#!/usr/bin/env python3
"""Fake YCSB launcher: honors -p redis.host/port, does PING round-trips, prints
standard Return= lines. `load` mode is a no-op; `run` mode measures for ~4s so a
mid-run disruption (at_s=1) lands inside the measured window."""
import socket, sys, time

def prop(name, default=""):
    for i, a in enumerate(sys.argv):
        if a == "-p" and i + 1 < len(sys.argv) and sys.argv[i + 1].startswith(name + "="):
            return sys.argv[i + 1].split("=", 1)[1]
    return default

mode = sys.argv[1]
if mode == "load":
    print("[OVERALL], RunTime(ms), 1")
    sys.exit(0)

host, port = prop("redis.host", "127.0.0.1"), int(prop("redis.port", "6379"))
ok = err = 0
deadline = time.time() + 4.0
sock = None
while time.time() < deadline:
    try:
        if sock is None:
            sock = socket.create_connection((host, port), timeout=1)
            sock.settimeout(1)
        sock.sendall(b"PING\\r\\n")
        if sock.recv(64).startswith(b"+PONG"):
            ok += 1
        else:
            err += 1
    except OSError:
        err += 1
        try:
            sock and sock.close()
        except OSError:
            pass
        sock = None  # reconnect next loop — client-side recovery
    time.sleep(0.05)

print("[OVERALL], RunTime(ms), 4000")
print(f"[OVERALL], Throughput(ops/sec), {(ok + err) / 4.0}")
print(f"[READ], Return=OK, {ok}")
if err:
    print(f"[READ], Return=ERROR, {err}")
'''


@pytest.mark.slow
def test_reliability_run_records_disruption_and_recovers(tmp_path, monkeypatch):
    if shutil.which("redis-server") is None:
        pytest.skip("redis-server not installed")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, rport = s.getsockname()
    s.close()
    redis = subprocess.Popen(
        ["redis-server", "--port", str(rport), "--save", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    fake = tmp_path / "ycsb"
    fake.write_text(_FAKE_YCSB)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{__import__('os').environ['PATH']}")
    monkeypatch.delenv("YCSB_HOME", raising=False)

    class _Handle:
        @staticmethod
        def binding() -> str:
            return "redis"

    try:
        # wait for redis
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", rport), timeout=0.5) as probe:
                    probe.sendall(b"PING\r\n")
                    if probe.recv(16).startswith(b"+PONG"):
                        break
            except OSError:
                time.sleep(0.2)

        suite = YcsbSuite()
        cfg = {
            "workload": "workloada",
            "recordcount": 10,
            "operationcount": 10,
            "reliability": {"action": "reset", "at_s": 1.0},
        }
        ds = suite.resolve(cfg, None)
        assert ds.payload["reliability"] == {"action": "reset", "at_s": 1.0}
        target = Target(mode="runtime", mock=False, handle=_Handle(), endpoint=f"127.0.0.1:{rport}")
        env = suite.prepare(target, ds, None)
        raw = suite.run(target, env, DriverContext(placement="local", trace_id="e" * 32))

        summary = json.loads(raw.path("summary").read_text())
        d = summary["disruption"]
        assert d["plan"]["action"] == "reset"
        assert d["fired"] is True
        assert d["connections_reset"] == 1  # ONE live logical connection was cut — no double count
        output = raw.path("ycsb_output").read_text()
        assert "Return=OK" in output  # ops succeeded before AND after (reconnect)
        # the trajectory carries the measured disruption span
        spans = [json.loads(x) for x in raw.path("trajectory").read_text().splitlines() if x.strip()]
        assert any(s["name"] == "ycsb.disruption.reset" for s in spans)

        # evaluator turns the evidence into measurements
        from clousight_bench.suites.ycsb.evaluator import OfficialYcsbEvaluator

        out = OfficialYcsbEvaluator().evaluate(raw)
        assert out["ycsb.completed_under_disruption"].value == 1.0
        assert "ycsb.error_rate" in out
    finally:
        redis.terminate()
        redis.wait(timeout=5)


def test_reliability_plan_folds_into_the_dataset_digest():
    suite = YcsbSuite()
    clean = suite.resolve({"workload": "workloada"}, None)
    disrupted = suite.resolve(
        {"workload": "workloada", "reliability": {"action": "reset", "at_s": 5.0}}, None
    )
    assert clean.digest != disrupted.digest  # a different disruption is a different benchmark
    assert "disrupt-reset" in disrupted.version


def test_reliability_plan_validation():
    suite = YcsbSuite()
    with pytest.raises(ValueError, match="reset|stall"):
        suite.resolve({"reliability": {"action": "explode"}}, None)
    with pytest.raises(ValueError, match="mapping"):
        suite.resolve({"reliability": "reset"}, None)
    with pytest.raises(ValueError, match="at_s"):
        suite.resolve({"reliability": {"action": "reset", "at_s": -1}}, None)
    with pytest.raises(ValueError, match="stall_ms"):
        suite.resolve({"reliability": {"action": "stall", "stall_ms": 0}}, None)


def test_clean_run_digest_is_stable_without_a_reliability_key():
    """Folding must be present-only: pre-R5 clean-run digests must not shift."""
    suite = YcsbSuite()
    a = suite.resolve({"workload": "workloada"}, None)
    b = suite.resolve({"workload": "workloada", "reliability": None}, None)
    assert a.digest == b.digest
    assert "disrupt" not in a.version


def test_reliability_refuses_to_run_clean_when_it_cannot_disrupt(tmp_path, monkeypatch):
    """binding != redis (or no endpoint) with a reliability plan must FAIL, not
    silently run a clean benchmark under a disruption-labeled dataset."""
    from clousight_bench.core.suite import EnvHandle

    fake = tmp_path / "ycsb"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    suite = YcsbSuite()
    env = EnvHandle(
        {
            "mock": False,
            "binary": str(fake),
            "binding": "basic",
            "props": [],
            "workload": "workloada",
            "recordcount": 1,
            "operationcount": 1,
            "reliability": {"action": "reset", "at_s": 5.0},
            "endpoint": "",
        }
    )
    target = Target(mode="runtime", mock=False, handle=None, endpoint=None)
    with pytest.raises(RuntimeError, match="refusing to run a clean benchmark"):
        suite.run(target, env, DriverContext(placement="local", trace_id="e" * 32))


def test_proxy_is_stopped_when_the_run_phase_fails(tmp_path):
    """The finally-block cleanup: a mid-run tool crash must still stop the proxy."""
    import subprocess

    from clousight_bench.core.suite import EnvHandle

    fake = tmp_path / "ycsb"
    fake.write_text('#!/bin/sh\nif [ "$1" = "load" ]; then exit 0; fi\nexit 3\n')
    fake.chmod(0o755)
    # a real listener so the proxy's upstream side is connectable
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    _, uport = srv.getsockname()
    suite = YcsbSuite()
    env = EnvHandle(
        {
            "mock": False,
            "binary": str(fake),
            "binding": "redis",
            "props": ["-p", "redis.host=127.0.0.1", "-p", f"redis.port={uport}"],
            "workload": "workloada",
            "recordcount": 1,
            "operationcount": 1,
            "reliability": {"action": "reset", "at_s": 60.0},
            "endpoint": f"127.0.0.1:{uport}",
        }
    )
    target = Target(mode="runtime", mock=False, handle=None, endpoint=f"127.0.0.1:{uport}")

    import clousight_bench.core.disruption as disruption_mod

    created: list[disruption_mod.DisruptionProxy] = []
    real_proxy = disruption_mod.DisruptionProxy

    class _Capturing(real_proxy):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    disruption_mod.DisruptionProxy = _Capturing  # type: ignore[misc]
    try:
        with pytest.raises(subprocess.CalledProcessError):
            suite.run(target, env, DriverContext(placement="local", trace_id="e" * 32))
    finally:
        disruption_mod.DisruptionProxy = real_proxy  # type: ignore[misc]
        srv.close()
    assert len(created) == 1
    proxy = created[0]
    # stopped: single-use refusal proves stop() ran (probing the closed port
    # with connect() is unreliable on Linux — an ephemeral source port can
    # collide with the target and TCP self-connect succeeds without a listener)
    with pytest.raises(RuntimeError, match="single-use"):
        proxy.start()
