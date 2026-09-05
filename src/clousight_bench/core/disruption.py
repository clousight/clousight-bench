"""Driver-side network disruption proxy — the fault-injection heritage, generalized.

A managed service under evaluation cannot (and must not) be fault-injected on
the server side; the DRIVER-side network path, however, belongs to the harness.
:class:`DisruptionProxy` is a plain TCP relay: the benchmark tool connects to
``127.0.0.1:<port>`` and the proxy forwards byte-for-byte to the real endpoint.
Mid-run the harness can inject:

* ``reset``   — close every active connection at once (a connection-loss event:
  what a failover, a rolling restart or an LB drain looks like from a client);
* ``stall``   — hold all forwarding for a fixed window (a network brown-out).

The proxy never fabricates protocol data — it only forwards, delays or closes.
What the tool reports afterwards (error counts, reconnects, completed run) is
therefore genuine SUT-client behavior under disruption, measured by the
recognized tool itself. Pure stdlib, thread-per-connection, works for any TCP
protocol (Redis/RESP, JDBC, HTTP).
"""

from __future__ import annotations

import copy
import socket
import threading
import time
from dataclasses import dataclass, field


@dataclass
class DisruptionStats:
    """What the proxy observed (evidence, not verdicts).

    Counts are per LOGICAL connection (one client<->upstream pair).
    ``stall_windows_unix_nano`` holds ``[start, end]`` wall-clock pairs for the
    forwarding gate the proxy actually enforced — the gate genuinely holds
    until ``end`` (a run that finishes earlier ends the effective window early;
    the suite clamps to run end when laying spans).
    """

    connections_total: int = 0
    connections_reset: int = 0
    stall_windows: int = 0
    stall_ms_total: float = 0.0
    disrupted_at_unix_nano: list[int] = field(default_factory=list)
    stall_windows_unix_nano: list[list[int]] = field(default_factory=list)


class DisruptionProxy:
    """TCP relay with injectable disruptions.

    Single-use: ``start()`` once, ``stop()`` once; restarting a stopped proxy
    raises (its accept loop is gone — a silently dead relay would forward
    nothing while looking alive).
    """

    def __init__(self, upstream_host: str, upstream_port: int, *, listen_host: str = "127.0.0.1") -> None:
        self._upstream = (upstream_host, upstream_port)
        self._listen_host = listen_host
        self._server: socket.socket | None = None
        # logical connections: one (client, upstream) pair each
        self._conns: set[tuple[socket.socket, socket.socket]] = set()
        self._lock = threading.Lock()
        self._stall_until = 0.0
        self._closing = False
        self._stats = DisruptionStats()
        self.port: int = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> str:
        """Bind an ephemeral port and serve; returns ``host:port`` for the tool."""
        if self._closing:
            raise RuntimeError("DisruptionProxy is single-use; create a new one instead of restarting")
        if self._server is not None:
            return f"{self._listen_host}:{self.port}"
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self._listen_host, 0))
        srv.listen(64)
        self._server = srv
        self.port = srv.getsockname()[1]
        threading.Thread(target=self._accept_loop, args=(srv,), name="disruption-proxy", daemon=True).start()
        return f"{self._listen_host}:{self.port}"

    def stop(self) -> None:
        self._closing = True
        srv, self._server = self._server, None
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for pair in conns:
            _close_pair(pair)

    def snapshot(self) -> DisruptionStats:
        """A consistent copy of the stats (safe against a concurrently firing timer)."""
        with self._lock:
            return copy.deepcopy(self._stats)

    # ------------------------------------------------------------------ injection
    def reset_connections(self) -> int:
        """Close every active connection NOW; returns how many were reset."""
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
            self._stats.connections_reset += len(conns)
            self._stats.disrupted_at_unix_nano.append(time.time_ns())
        for pair in conns:
            _close_pair(pair)
        return len(conns)

    def stall(self, stall_ms: float) -> None:
        """Hold all forwarding for *stall_ms* (new bytes wait out the window)."""
        start_ns = time.time_ns()
        self._stall_until = time.monotonic() + stall_ms / 1000.0
        with self._lock:
            self._stats.stall_windows += 1
            self._stats.stall_ms_total += float(stall_ms)
            self._stats.disrupted_at_unix_nano.append(start_ns)
            self._stats.stall_windows_unix_nano.append([start_ns, start_ns + int(stall_ms * 1_000_000)])

    # ------------------------------------------------------------------ internals
    def _accept_loop(self, srv: socket.socket) -> None:
        while not self._closing:
            try:
                client, _ = srv.accept()
            except OSError:
                return  # closed
            # Upstream handshake off the accept thread: a slow/down upstream
            # must not serialize every other client behind a connect timeout.
            threading.Thread(target=self._handshake, args=(client,), daemon=True).start()

    def _handshake(self, client: socket.socket) -> None:
        try:
            upstream = socket.create_connection(self._upstream, timeout=10)
        except OSError:
            try:
                client.close()
            except OSError:
                pass
            return
        pair = (client, upstream)
        with self._lock:
            if self._closing:
                _close_pair(pair)
                return
            self._conns.add(pair)
            self._stats.connections_total += 1
        for src, dst in (pair, pair[::-1]):
            threading.Thread(target=self._pump, args=(src, dst, pair), daemon=True).start()

    def _pump(
        self, src: socket.socket, dst: socket.socket, pair: tuple[socket.socket, socket.socket]
    ) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                wait = self._stall_until - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            _close_pair(pair)
            with self._lock:
                self._conns.discard(pair)


def _close_pair(pair: tuple[socket.socket, socket.socket]) -> None:
    for sock in pair:
        try:
            sock.close()
        except OSError:
            pass


def schedule_disruption(
    proxy: DisruptionProxy, *, action: str, at_s: float, stall_ms: float = 0.0
) -> threading.Timer:
    """Arm *action* (``reset`` | ``stall``) to fire ``at_s`` seconds from now."""
    if action == "reset":
        timer = threading.Timer(at_s, proxy.reset_connections)
    elif action == "stall":
        timer = threading.Timer(at_s, proxy.stall, kwargs={"stall_ms": stall_ms})
    else:
        raise ValueError(f"unknown disruption action {action!r} (expected reset|stall)")
    timer.daemon = True
    timer.start()
    return timer
