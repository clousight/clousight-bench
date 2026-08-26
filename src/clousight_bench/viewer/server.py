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
    /api/meta                           results_dir basename, version, record count
    /api/records                        list_records summaries
    /api/record/<run_id>                full record dict
    /api/record/<run_id>/trajectory     parsed spans + t0
    anything else                       404 {"error": ...} (hash router: no SPA fallback)
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from clousight_bench import __version__
from clousight_bench.viewer.data import count_records, list_records, load_record, load_trajectory

logger = logging.getLogger(__name__)

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


def create_server(results_dir: Path, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """A ready-to-serve ThreadingHTTPServer; port=0 picks an ephemeral port."""
    allowed_hosts = {host.lower(), "localhost", "127.0.0.1", "[::1]"}

    class ViewerHandler(BaseHTTPRequestHandler):
        server_version = "csbench-viewer"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib signature)
            logger.debug("viewer: " + format, *args)

        def do_GET(self) -> None:
            self._route(head_only=False)

        def do_HEAD(self) -> None:
            self._route(head_only=True)

        def _route(self, head_only: bool) -> None:
            raw_path = urlsplit(self.path).path
            try:
                self._respond(raw_path, head_only)
            except ConnectionError:  # includes BrokenPipeError: client went away mid-write
                logger.debug("viewer: client disconnected during %s", raw_path)
            except Exception:  # never let a handler bug kill the connection silently
                logger.exception("viewer: error handling %s", raw_path)
                try:
                    self._send_json(500, {"error": "internal server error"}, head_only)
                except ConnectionError:  # client also gone before the 500 could be sent
                    logger.debug("viewer: client disconnected before 500 for %s", raw_path)

        def _host_allowed(self) -> bool:
            value = self.headers.get("Host")
            return value is not None and _host_without_port(value) in allowed_hosts

        def _respond(self, raw_path: str, head_only: bool) -> None:
            if not self._host_allowed():  # DNS-rebinding guard: 403 before any routing
                self._send_json(403, {"error": "host not allowed"}, head_only)
                return
            # Decode per-segment so an encoded "/" stays inside its segment and
            # is rejected by the per-segment guards instead of splitting.
            segments = [unquote(seg) for seg in raw_path.split("/")[1:]]
            if segments[:1] != ["api"]:
                if raw_path in ("/", "/index.html"):
                    segments = ["index.html"]
                asset = _dist_asset(segments)
                if asset is None:
                    self._send_json(404, {"error": f"no such endpoint: {raw_path}"}, head_only)
                else:
                    body, content_type = asset
                    self._send(200, body, content_type, head_only)
                return
            if segments == ["api", "meta"]:
                meta = {
                    "results_dir": results_dir.name,  # basename only, never the full path
                    "version": __version__,
                    "counts": {"records": count_records(results_dir)},
                }
                self._send_json(200, meta, head_only)
                return
            if segments == ["api", "records"]:
                self._send_json(200, list_records(results_dir), head_only)
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

        def _send_json(self, status: int, payload: Any, head_only: bool) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", head_only)

        def _send(self, status: int, body: bytes, content_type: str, head_only: bool) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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
