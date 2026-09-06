"""Local read-only HTTP viewer over a results directory (stdlib only).

Serves the packaged single-page viewer (the committed Vite build under
resources/viewer/dist) plus a tiny JSON API on top of viewer/data.py. Strictly
read-only, binds to 127.0.0.1 by default, and never logs per-request lines to
stderr (log_message is routed to logging.debug).

Every response carries strict security headers (CSP, nosniff, no-referrer),
and requests whose Host header is not the bound host / a localhost alias are
rejected with a 403 (DNS-rebinding guard).

Routes:
    /  /index.html                      dist/index.html
    /assets/<hashed file>               dist files (safe segment-checked join,
                                        extension->content-type allowlist)
    /api/meta                           results_dir basename, version, counts
    /api/records                        list_records summaries
    /api/board                          domain -> suite board, newest run each
    /api/suite/<domain>/<suite_id>      one suite's platforms + history
    /api/record/<run_id>                full record dict
    /api/record/<run_id>/trajectory     parsed spans + t0 (+ source)
    /api/progress                       live/recent run snapshots
    /api/progress/<run_id>              snapshot + events (?since=<seq>)
    /api/progress/<run_id>/stream       Server-Sent Events (?since=<seq>)
    POST /api/progress/<run_id>/cancel  request cancellation (see below)
    anything else                       404 {"error": ...} (hash router: no SPA fallback)

The cancel endpoint is the only mutating route this server has ever had, so it
is fenced accordingly: it shares the Host guard with GET, demands the custom
``X-Csbench-Progress: 1`` header (which no plain HTML form can set, closing
simple-form CSRF), refuses a request body, and can only ever create the
zero-byte marker ``progress.request_cancel`` writes for a still-running run.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from clousight_bench import __version__
from clousight_bench.core import progress
from clousight_bench.core.logsafe import sanitize_for_log
from clousight_bench.viewer.data import (
    count_records,
    list_records,
    load_board,
    load_record,
    load_suite,
    load_trajectory,
)

logger = logging.getLogger(__name__)

#: SSE tuning. Each stream owns a ThreadingHTTPServer thread for its whole life,
#: so the cap is a real resource bound, not decoration: eight open tabs is far
#: more than a local viewer needs, and the lifetime cap stops a tab left open
#: over a weekend from pinning a thread forever.
_MAX_STREAMS = 8
_STREAM_MAX_S = 6 * 60 * 60.0
_STREAM_POLL_S = 0.4
#: Idle proxies drop a silent connection; a comment line keeps it warm.
_HEARTBEAT_S = 15.0
#: The header a cancel request must carry. Custom headers are unreachable from
#: a cross-origin <form>, which is the CSRF shape that actually threatens a
#: server bound to localhost.
_CANCEL_HEADER = "X-Csbench-Progress"


class _StreamSlots:
    """A counting gate over the concurrent SSE streams."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._held = 0

    def acquire(self) -> bool:
        with self._lock:
            if self._held >= self._limit:
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._held = max(0, self._held - 1)


#: Module-level on purpose: the bound is per process, not per server instance,
#: because the threads being protected belong to the process.
_STREAM_SLOTS = _StreamSlots(_MAX_STREAMS)

# Extension -> content type for files under the packaged dist tree. Anything
# outside this map is a 404: the dist build only ever emits these kinds.
_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _dist_asset(segments: list[str]) -> tuple[bytes, str] | None:
    """Bytes + content type for a path under the packaged dist tree, else None.

    Containment is enforced BEFORE any joining: every (already URL-decoded)
    segment must be a plain filename piece — reject empty segments, ".", "..",
    NUL bytes, path separators, and colons (a ``c:x`` segment joins as a
    drive-relative path on Windows, escaping the tree) — so no request shape
    can step outside resources/viewer/dist. The final segment's extension must
    be in the content-type allowlist. Installed-safe via importlib.resources
    traversal.
    """
    if not segments:
        return None
    for segment in segments:
        if (
            not segment
            or segment in {".", ".."}
            or "\x00" in segment
            or "/" in segment
            or "\\" in segment
            or ":" in segment
        ):
            return None
    content_type = _CONTENT_TYPES.get(PurePosixPath(segments[-1]).suffix)
    if content_type is None:
        return None
    resource = files("clousight_bench.resources").joinpath("viewer").joinpath("dist")
    for segment in segments:
        resource = resource.joinpath(segment)
    try:
        return resource.read_bytes(), content_type
    except OSError:  # missing file, or a directory where a file was expected
        return None


def _host_without_port(value: str) -> str:
    """The lowercased host part of a Host header, optional :port stripped.

    Fail-closed: any suffix that is not empty or ``:<digits>`` yields "" so
    junk like ``localhost:evil`` or ``[::1]evil`` never matches the allowlist.
    """
    value = value.strip().lower()
    if value.startswith("["):  # bracketed IPv6: port (if any) follows the "]"
        end = value.find("]")
        if end == -1:
            return ""
        rest = value[end + 1 :]
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return ""
        return value[: end + 1]
    if ":" in value:
        hostpart, _, portpart = value.rpartition(":")
        return hostpart if portpart.isdigit() else ""
    return value


#: Cache policy. The asset filenames are content-hashed by the Vite build, so
#: they can be cached forever; index.html names them and therefore must never
#: be. Without this, upgrading csbench and reloading serves a cached index.html
#: pointing at an asset hash the new wheel does not contain — a white page with
#: nothing in the console to explain it. Everything else (the JSON API, errors)
#: defaults to no-store: it is all live state.
_NO_STORE = "no-store"
_IMMUTABLE = "public, max-age=31536000, immutable"


def _cache_policy(segments: list[str]) -> str:
    """Long-cache the content-hashed assets; never cache the document."""
    return _IMMUTABLE if segments[:1] == ["assets"] else _NO_STORE


def create_server(results_dir: Path, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """A ready-to-serve ThreadingHTTPServer; port=0 picks an ephemeral port."""
    allowed_hosts = {host.lower(), "localhost", "127.0.0.1", "[::1]"}
    # A machine that lost power mid-benchmark leaves a snapshot still claiming
    # to run. Collect those once here so the viewer does not open on a phantom.
    progress.reap_stale(results_dir)

    class ViewerHandler(BaseHTTPRequestHandler):
        server_version = "csbench-viewer"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
            # The stdlib passes the raw request line through *args, so the
            # rendered message is client-controlled: sanitize it, and render it
            # eagerly only when someone is actually listening at DEBUG.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("viewer: %s", sanitize_for_log(format % args if args else format))

        def do_GET(self) -> None:
            self._route(head_only=False)

        def do_HEAD(self) -> None:
            self._route(head_only=True)

        def do_POST(self) -> None:
            self._route(head_only=False, mutating=True)

        def _route(self, head_only: bool, *, mutating: bool = False) -> None:
            raw_path = urlsplit(self.path).path
            try:
                # The DNS-rebinding guard lives here, above the method split, so
                # POST can never end up behind a differently-worded copy of it.
                if not self._host_allowed():
                    self._send_json(403, {"error": "host not allowed"}, head_only)
                elif mutating:
                    self._respond_post(raw_path)
                else:
                    self._respond(raw_path, head_only)
            except ConnectionError:  # includes BrokenPipeError: client went away mid-write
                logger.debug("viewer: client disconnected during %s", sanitize_for_log(raw_path))
            except Exception:  # never let a handler bug kill the connection silently
                logger.exception("viewer: error handling %s", sanitize_for_log(raw_path))
                try:
                    self._send_json(500, {"error": "internal server error"}, head_only)
                except ConnectionError:  # client also gone before the 500 could be sent
                    logger.debug("viewer: client disconnected before 500 for %s", sanitize_for_log(raw_path))

        def _host_allowed(self) -> bool:
            value = self.headers.get("Host")
            return value is not None and _host_without_port(value) in allowed_hosts

        def _segments(self, raw_path: str) -> list[str]:
            """Path segments, decoded per-segment so an encoded "/" stays inside
            its segment and is rejected by the per-segment guards, not split on."""
            return [unquote(seg) for seg in raw_path.split("/")[1:]]

        def _query_int(self, name: str) -> int:
            """A non-negative int query parameter; anything else reads as 0."""
            values = parse_qs(urlsplit(self.path).query).get(name) or []
            try:
                return max(0, int(values[0]))
            except (IndexError, ValueError):
                return 0

        def _respond(self, raw_path: str, head_only: bool) -> None:
            segments = self._segments(raw_path)
            if segments[:1] != ["api"]:
                if raw_path in ("/", "/index.html"):
                    segments = ["index.html"]
                asset = _dist_asset(segments)
                if asset is None:
                    self._send_json(404, {"error": f"no such endpoint: {raw_path}"}, head_only)
                else:
                    body, content_type = asset
                    self._send(200, body, content_type, head_only, cache=_cache_policy(segments))
                return
            if segments == ["api", "meta"]:
                meta = {
                    "results_dir": results_dir.name,  # basename only, never the full path
                    "version": __version__,
                    "counts": {"records": count_records(results_dir)},
                    "progress_active": len(progress.list_active(results_dir)),
                }
                self._send_json(200, meta, head_only)
                return
            if segments == ["api", "records"]:
                self._send_json(200, list_records(results_dir), head_only)
                return
            if segments == ["api", "board"]:
                self._send_json(200, load_board(results_dir), head_only)
                return
            if len(segments) == 4 and segments[:2] == ["api", "suite"]:
                suite = load_suite(results_dir, segments[2], segments[3])
                if suite is None:
                    self._send_json(
                        404, {"error": f"no runs for suite: {segments[2]}/{segments[3]}"}, head_only
                    )
                else:
                    self._send_json(200, suite, head_only)
                return
            if segments == ["api", "progress"]:
                self._send_json(200, {"runs": progress.list_active(results_dir)}, head_only)
                return
            if len(segments) == 3 and segments[:2] == ["api", "progress"] and segments[2]:
                self._progress_snapshot(segments[2], head_only)
                return
            if (
                len(segments) == 4
                and segments[:2] == ["api", "progress"]
                and segments[2]
                and segments[3] == "stream"
            ):
                self._progress_stream(segments[2], head_only)
                return
            if len(segments) == 3 and segments[:2] == ["api", "record"] and segments[2]:
                record = load_record(results_dir, segments[2])
                if record is None:
                    self._send_json(404, {"error": f"unknown run_id: {segments[2]}"}, head_only)
                else:
                    self._send_json(200, record, head_only)
                return
            if (
                len(segments) == 4
                and segments[:2] == ["api", "record"]
                and segments[2]
                and segments[3] == "trajectory"
            ):
                trajectory = load_trajectory(results_dir, segments[2])
                if trajectory is None:
                    self._send_json(404, {"error": f"no trajectory for run_id: {segments[2]}"}, head_only)
                else:
                    self._send_json(200, trajectory, head_only)
                return
            self._send_json(404, {"error": f"no such endpoint: {raw_path}"}, head_only)

        # ----------------------------------------------------------------
        # Progress plane
        # ----------------------------------------------------------------

        def _progress_snapshot(self, run_id: str, head_only: bool) -> None:
            state = progress.read_state(results_dir, run_id)
            if state is None:
                self._send_json(404, {"error": f"unknown run_id: {run_id}"}, head_only)
                return
            events = progress.read_events(results_dir, run_id, since_seq=self._query_int("since"))
            self._send_json(200, {"state": state, "events": events}, head_only)

        def _progress_stream(self, run_id: str, head_only: bool) -> None:
            state = progress.read_state(results_dir, run_id)
            if state is None:
                self._send_json(404, {"error": f"unknown run_id: {run_id}"}, head_only)
                return
            if head_only:  # a HEAD must not occupy a slot for hours
                self._send(200, b"", "text/event-stream; charset=utf-8", head_only)
                return
            if not _STREAM_SLOTS.acquire():
                logger.warning("viewer: refusing progress stream for %s: at cap", sanitize_for_log(run_id))
                self._send_json(503, {"error": "too many progress streams"}, head_only)
                return
            try:
                self._pump_events(run_id, self._query_int("since"), state)
            except (BrokenPipeError, ConnectionResetError):
                # A closed tab is the normal way an SSE stream ends, not a fault.
                logger.debug("viewer: progress stream closed by client")
            finally:
                _STREAM_SLOTS.release()

        def _pump_events(self, run_id: str, since: int, state: dict[str, Any]) -> None:
            """Serve one SSE subscription until the run ends or the cap expires.

            Polls the two files rather than watching them: the progress plane is
            deliberately plain files with no notification channel, and a 400ms
            stat is cheaper than a portable watcher would be.
            """
            self._begin_sse()
            deadline = time.monotonic() + _STREAM_MAX_S
            self._sse("state", state)
            stamp = (state.get("seq"), state.get("updated_at"))
            since = self._drain(run_id, since)
            last_write = time.monotonic()
            status = str(state.get("status") or "")
            record_path = state.get("record_path")

            while status not in progress.TERMINAL_STATUSES and time.monotonic() < deadline:
                time.sleep(_STREAM_POLL_S)
                fresh = progress.read_state(results_dir, run_id)
                if fresh is None:
                    # The directory was reaped underneath us. Say what we last
                    # knew rather than leaving the client hanging on a run that
                    # no longer exists anywhere.
                    self._sse("done", {"status": status, "record_path": record_path})
                    return
                fresh_stamp = (fresh.get("seq"), fresh.get("updated_at"))
                if fresh_stamp != stamp:
                    self._sse("state", fresh)
                    stamp = fresh_stamp
                    last_write = time.monotonic()
                new_since = self._drain(run_id, since)
                if new_since != since:
                    since = new_since
                    last_write = time.monotonic()
                status = str(fresh.get("status") or "")
                record_path = fresh.get("record_path")
                if time.monotonic() - last_write >= _HEARTBEAT_S:
                    self._write_frame(b": heartbeat\n\n")
                    last_write = time.monotonic()

            self._drain(run_id, since)  # whatever landed alongside the terminal state
            self._sse("done", {"status": status, "record_path": record_path})

        def _drain(self, run_id: str, since: int) -> int:
            """Emit any events past ``since``; return the new high-water seq."""
            events = progress.read_events(results_dir, run_id, since_seq=since)
            if not events:
                return since
            self._sse("events", {"events": events})
            return max(since, *(int(event.get("seq") or 0) for event in events))

        def _begin_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # nginx et al: do not buffer
            self.send_header("Connection", "close")
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.flush()

        def _sse(self, event: str, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str)
            self._write_frame(f"event: {event}\ndata: {body}\n\n".encode())

        def _write_frame(self, frame: bytes) -> None:
            self.wfile.write(frame)
            self.wfile.flush()  # unbuffered: a live view that arrives in batches is not live

        # ----------------------------------------------------------------
        # POST: cancel, and nothing else
        # ----------------------------------------------------------------

        def _respond_post(self, raw_path: str) -> None:
            segments = self._segments(raw_path)
            if (
                len(segments) == 4
                and segments[:2] == ["api", "progress"]
                and segments[2]
                and segments[3] == "cancel"
            ):
                self._cancel(segments[2])
                return
            self._send_json(404, {"error": f"no such endpoint: {raw_path}"}, False)

        def _cancel(self, run_id: str) -> None:
            if self.headers.get(_CANCEL_HEADER) != "1":
                logger.warning(
                    "viewer: cancel for %s rejected: missing %s header",
                    sanitize_for_log(run_id),
                    _CANCEL_HEADER,
                )
                self._send_json(403, {"error": f"missing {_CANCEL_HEADER}: 1 header"}, False)
                return
            if (self.headers.get("Content-Length") or "0") != "0":
                # Nothing here reads a body, and accepting one would leave a
                # request whose meaning depends on bytes we never looked at.
                self._send_json(400, {"error": "cancel takes no request body"}, False)
                return
            if not progress.valid_run_id(run_id):
                # Not a plain token: rejected before it can name anything on disk.
                logger.warning("viewer: cancel rejected for malformed run_id %s", sanitize_for_log(run_id))
                self._send_json(404, {"error": "unknown run_id"}, False)
                return
            if not progress.request_cancel(results_dir, run_id):
                self._send_json(404, {"error": f"no cancellable run: {run_id}"}, False)
                return
            logger.info("viewer: cancel requested for run %s", sanitize_for_log(run_id))
            self._send_json(200, {"cancelled": True}, False)

        def _send_json(self, status: int, payload: Any, head_only: bool) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", head_only)

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            head_only: bool,
            cache: str = _NO_STORE,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            for name, value in _SECURITY_HEADERS.items():  # centrally: every response kind
                self.send_header(name, value)
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), ViewerHandler)
    server.daemon_threads = True
    return server


def serve_until_interrupt(server: ThreadingHTTPServer) -> None:
    """Serve an already-bound server until interrupted; Ctrl-C shuts down cleanly."""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.debug("viewer: interrupted, shutting down")
    finally:
        server.server_close()


def serve(results_dir: Path, host: str = "127.0.0.1", port: int = 8787) -> None:
    """Bind + serve until interrupted (create_server then serve_until_interrupt)."""
    serve_until_interrupt(create_server(results_dir, host=host, port=port))
