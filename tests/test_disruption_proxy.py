"""The driver-side disruption proxy: forwards honestly, disrupts on command."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from clousight_bench.core.disruption import DisruptionProxy, schedule_disruption


def _echo_server() -> tuple[str, int, socket.socket]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    host, port = srv.getsockname()

    def _serve() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return

            def _echo(c: socket.socket) -> None:
                try:
                    while True:
                        data = c.recv(4096)
                        if not data:
                            return
                        c.sendall(data)
                except OSError:
                    pass
                finally:
                    c.close()

            threading.Thread(target=_echo, args=(conn,), daemon=True).start()

    threading.Thread(target=_serve, daemon=True).start()
    return host, port, srv


def _roundtrip(endpoint: str, payload: bytes, timeout: float = 3.0) -> bytes:
    host, port = endpoint.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        return sock.recv(4096)


def test_proxy_forwards_byte_for_byte():
    host, port, srv = _echo_server()
    proxy = DisruptionProxy(host, port)
    endpoint = proxy.start()
    try:
        assert _roundtrip(endpoint, b"hello-r5") == b"hello-r5"
        assert proxy.snapshot().connections_total == 1
    finally:
        proxy.stop()
        srv.close()


def test_reset_closes_active_connections():
    host, port, srv = _echo_server()
    proxy = DisruptionProxy(host, port)
    endpoint = proxy.start()
    phost, pport = endpoint.rsplit(":", 1)
    try:
        sock = socket.create_connection((phost, int(pport)), timeout=3)
        sock.settimeout(3)
        sock.sendall(b"a")
        assert sock.recv(16) == b"a"
        n = proxy.reset_connections()
        assert n == 1  # ONE logical connection was live — the count must not double
        # the live connection is now dead: recv returns b"" or raises
        sock.sendall(b"b")
        try:
            assert sock.recv(16) == b""
        except OSError:
            pass
        sock.close()
        stats = proxy.snapshot()
        assert stats.connections_reset == 1
        assert len(stats.disrupted_at_unix_nano) == 1
        # new connections still work (the endpoint recovers)
        assert _roundtrip(endpoint, b"after") == b"after"
    finally:
        proxy.stop()
        srv.close()


def test_stall_delays_forwarding():
    host, port, srv = _echo_server()
    proxy = DisruptionProxy(host, port)
    endpoint = proxy.start()
    try:
        proxy.stall(400)
        t0 = time.monotonic()
        assert _roundtrip(endpoint, b"slow") == b"slow"
        assert time.monotonic() - t0 >= 0.35
        stats = proxy.snapshot()
        assert stats.stall_windows == 1
        start_ns, end_ns = stats.stall_windows_unix_nano[0]
        assert end_ns - start_ns == 400 * 1_000_000  # the gate-enforced window
    finally:
        proxy.stop()
        srv.close()


def test_schedule_disruption_fires_and_rejects_unknown():
    host, port, srv = _echo_server()
    proxy = DisruptionProxy(host, port)
    endpoint = proxy.start()
    phost, pport = endpoint.rsplit(":", 1)
    try:
        sock = socket.create_connection((phost, int(pport)), timeout=3)
        sock.sendall(b"x")
        schedule_disruption(proxy, action="reset", at_s=0.1)
        deadline = time.monotonic() + 3
        while proxy.snapshot().connections_reset == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert proxy.snapshot().connections_reset == 1
        sock.close()
        with pytest.raises(ValueError, match="unknown disruption action"):
            schedule_disruption(proxy, action="explode", at_s=0.0)
    finally:
        proxy.stop()
        srv.close()


@pytest.mark.slow
def test_disruption_against_a_real_redis(tmp_path):
    """Genuine end-to-end: RESP PING through the proxy, reset mid-session, reconnect works."""
    import shutil
    import subprocess

    if shutil.which("redis-server") is None:
        pytest.skip("redis-server not installed")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, rport = s.getsockname()
    s.close()
    proc = subprocess.Popen(
        ["redis-server", "--port", str(rport), "--save", ""],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proxy = DisruptionProxy("127.0.0.1", rport)
    try:
        endpoint = proxy.start()
        deadline = time.monotonic() + 10
        reply = b""
        while time.monotonic() < deadline:
            try:
                reply = _roundtrip(endpoint, b"PING\r\n")
                if reply.startswith(b"+PONG"):
                    break
            except OSError:
                time.sleep(0.2)
        assert reply.startswith(b"+PONG")
        proxy.reset_connections()
        # a NEW connection through the proxy still reaches redis (recovery)
        assert _roundtrip(endpoint, b"PING\r\n").startswith(b"+PONG")
    finally:
        proxy.stop()
        proc.terminate()
        proc.wait(timeout=5)


def test_stopped_proxy_refuses_restart():
    host, port, srv = _echo_server()
    proxy = DisruptionProxy(host, port)
    proxy.start()
    proxy.stop()
    try:
        with pytest.raises(RuntimeError, match="single-use"):
            proxy.start()
    finally:
        srv.close()
