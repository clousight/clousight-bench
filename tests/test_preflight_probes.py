"""Connectivity + upstream-tool probes: doctor answers reachability/tooling BEFORE a run."""

from __future__ import annotations

import socket
import threading

import pytest

from clousight_bench.core import preflight as pf


def _serve_once(reply: bytes) -> tuple[str, int, threading.Thread]:
    """One-shot TCP server on an ephemeral port; replies *reply* to any recv."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    host, port = srv.getsockname()

    def _run() -> None:
        try:
            conn, _ = srv.accept()
            conn.settimeout(2)
            try:
                conn.recv(64)
                if reply:
                    conn.sendall(reply)
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return host, port, t


def test_tcp_reachable_ok():
    host, port, t = _serve_once(b"")
    check = pf.tcp_reachable_check("db", f"{host}:{port}")
    t.join(timeout=3)
    assert check.ok and "reachable" in check.detail


def test_tcp_unreachable_is_critical_with_remediation():
    # bind then close -> port is free/refused
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    check = pf.tcp_reachable_check("db", f"127.0.0.1:{port}", timeout_s=0.5)
    assert not check.ok and check.severity == pf.CRITICAL
    assert "allowlist" in check.remediation


def test_tcp_bad_endpoint_shape():
    check = pf.tcp_reachable_check("db", "not-an-endpoint")
    assert not check.ok and "host:port" in check.detail


def test_resp_ping_pong():
    host, port, t = _serve_once(b"+PONG\r\n")
    check = pf.resp_ping_check("redis", f"{host}:{port}")
    t.join(timeout=3)
    assert check.ok and "PONG" in check.detail


def test_resp_noauth_still_proves_a_live_service():
    host, port, t = _serve_once(b"-NOAUTH Authentication required.\r\n")
    check = pf.resp_ping_check("redis", f"{host}:{port}")
    t.join(timeout=3)
    assert check.ok and "auth required" in check.detail


def test_resp_non_redis_service_fails():
    host, port, t = _serve_once(b"HTTP/1.1 400 Bad Request\r\n")
    check = pf.resp_ping_check("redis", f"{host}:{port}")
    t.join(timeout=3)
    assert not check.ok and "not speaking RESP" in check.detail


def test_java_version_check_parses_local_java(monkeypatch):
    class _Proc:
        stderr = 'openjdk version "17.0.9" 2023-10-17\n'
        stdout = ""

    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/java")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    ok = pf.java_version_check("java", min_major=17, hint="install 17")
    assert ok.ok and "17" in ok.detail


def test_java_legacy_1_8_scheme_and_too_old(monkeypatch):
    class _Proc:
        stderr = 'openjdk version "1.8.0_382"\n'
        stdout = ""

    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/java")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    check = pf.java_version_check("java", min_major=11, hint="install 11+")
    assert not check.ok and check.severity == pf.CRITICAL
    assert "java 8" in check.detail


def test_java_missing(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    check = pf.java_version_check("java", min_major=17, hint="install 17")
    assert not check.ok and "no `java`" in check.detail


@pytest.mark.slow
def test_resp_ping_against_a_real_redis(tmp_path):
    """Genuine end-to-end: spin a real redis-server, probe it."""
    import shutil
    import subprocess
    import time

    if shutil.which("redis-server") is None:
        pytest.skip("redis-server not installed")
    proc = subprocess.Popen(
        ["redis-server", "--port", "0", "--unixsocket", str(tmp_path / "r.sock"), "--save", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # port 0 => random; simpler: relaunch on a fixed free port
        proc.terminate()
        proc.wait(timeout=5)
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        _, port = s.getsockname()
        s.close()
        proc = subprocess.Popen(
            ["redis-server", "--port", str(port), "--save", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        check = None
        while time.time() < deadline:
            check = pf.resp_ping_check("redis", f"127.0.0.1:{port}")
            if check.ok:
                break
            time.sleep(0.2)
        assert check is not None and check.ok, f"redis probe failed: {check and check.detail}"
    finally:
        proc.terminate()
        proc.wait(timeout=5)
