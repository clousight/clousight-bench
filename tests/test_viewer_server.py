"""Tests for the local read-only viewer HTTP server (viewer/server.py) + `csbench serve`.

The server is exercised for real: create_server on an ephemeral port (port=0),
serve_forever on a daemon thread, requests via http.client, shutdown in finally.
Records are seeded by hand in the on-disk layout the reader expects
(results_dir/<domain>/<adapter>/<task_id>-<run_id>.json, artifacts under
results_dir/artifacts/).

Static serving is the committed Vite build (resources/viewer/dist): `/` and
`/index.html` serve the dist index, hashed `/assets/*` files are served with a
strict extension->content-type map, and everything else (traversal, unknown
extensions, extension-less paths — hash router, so no SPA fallback) is a JSON
404 without any path escaping dist.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Any

import pytest

from clousight_bench.viewer.server import create_server, serve

RUN_ID = "run-abc123"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_results(results_dir: Path) -> dict[str, Any]:
    """Write one complete record + trajectory artifact; return the record dict."""
    record: dict[str, Any] = {
        "run": {"run_id": RUN_ID, "started_at": "2026-01-01T00:00:00Z"},
        "identity": {"domain": "agent-runtime", "task_id": "t1", "adapter": "local-sim"},
        "status": "completed",
        "provenance": {"suite_id": "swe-bench", "scaffold": "mini"},
        "measurements": {"swe-bench.resolved": {"value": 1.0, "unit": "ratio"}},
        "artifacts": [
            {
                "kind": "trajectory",
                "media": "application/jsonl",
                "sha256": "sha256:0",
                "path": "t1/trajectory.jsonl",
            }
        ],
    }
    record_dir = results_dir / "agent-runtime" / "local-sim"
    record_dir.mkdir(parents=True)
    (record_dir / f"t1-{RUN_ID}.json").write_text(json.dumps(record), encoding="utf-8")

    span = {
        "span_id": "s1",
        "trace_id": "tr1",
        "parent_id": None,
        "name": "step",
        "kind": "tool_call",
        "t_start": 1.0,
        "t_end": 2.0,
        "status": "ok",
        "attrs": {},
    }
    artifact_dir = results_dir / "artifacts" / "t1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "trajectory.jsonl").write_text(json.dumps(span) + "\n", encoding="utf-8")
    return record


@pytest.fixture()
def seeded_record(tmp_path: Path) -> dict[str, Any]:
    return _seed_results(tmp_path / "results")


@pytest.fixture()
def server(tmp_path: Path, seeded_record: dict[str, Any]) -> Iterator[ThreadingHTTPServer]:
    srv = create_server(tmp_path / "results", host="127.0.0.1", port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _request(srv: ThreadingHTTPServer, path: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    """One HTTP request against the test server; the raw path is sent verbatim."""
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _request_with_host(
    srv: ThreadingHTTPServer, path: str, host_value: str | None
) -> tuple[int, dict[str, str], bytes]:
    """GET with an explicit Host header value; None sends no Host header at all."""
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", path, skip_host=True)
        if host_value is not None:
            conn.putheader("Host", host_value)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


def test_api_records_lists_seeded_record(server: ThreadingHTTPServer) -> None:
    status, headers, body = _request(server, "/api/records")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert int(headers["content-length"]) == len(body)
    summaries = json.loads(body)
    assert len(summaries) == 1
    assert summaries[0]["run_id"] == RUN_ID
    assert summaries[0]["has_trajectory"] is True
    # query strings are tolerated: routing looks at the path component only
    status_q, _, body_q = _request(server, "/api/records?refresh=1&x=%2F")
    assert status_q == 200
    assert json.loads(body_q) == summaries


def test_api_record_returns_full_dict(server: ThreadingHTTPServer, seeded_record: dict[str, Any]) -> None:
    status, headers, body = _request(server, f"/api/record/{RUN_ID}")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(body) == seeded_record


def test_api_trajectory_returns_spans(server: ThreadingHTTPServer) -> None:
    status, _, body = _request(server, f"/api/record/{RUN_ID}/trajectory")
    assert status == 200
    traj = json.loads(body)
    assert len(traj["spans"]) == 1
    assert traj["spans"][0]["span_id"] == "s1"
    assert traj["t0"] == 1.0


def test_unknown_run_id_is_json_404(server: ThreadingHTTPServer) -> None:
    for path in ("/api/record/run-nope", "/api/record/run-nope/trajectory"):
        status, headers, body = _request(server, path)
        assert status == 404
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


def test_traversal_in_url_is_404(server: ThreadingHTTPServer) -> None:
    """Encoded slashes in the run_id segment must never reach the filesystem."""
    status, _, body = _request(server, "/api/record/..%2f..%2fetc/trajectory")
    assert status == 404
    assert "error" in json.loads(body)


def test_unknown_paths_are_json_404(server: ThreadingHTTPServer) -> None:
    # /api/record//trajectory: the empty run_id segment is rejected by routing
    # (symmetric with /api/record/), never reaching the data layer.
    for path in (
        "/favicon.ico",
        "/api/nope",
        "/api/record/",
        "/api/record//trajectory",
        "/etc/passwd",
    ):
        status, headers, body = _request(server, path)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Static page (built dist/index.html)
# ---------------------------------------------------------------------------


def _dist_asset_urls() -> list[str]:
    """URL paths of the hashed files under the packaged dist/assets directory."""
    assets = resource_files("clousight_bench.resources").joinpath("viewer", "dist", "assets")
    return sorted(f"/assets/{entry.name}" for entry in assets.iterdir() if entry.is_file())


def _hashed_asset_url(suffix: str) -> str:
    for url in _dist_asset_urls():
        if url.endswith(suffix):
            return url
    raise AssertionError(f"no dist asset with suffix {suffix!r}; run scripts/build_viewer.sh")


def test_index_served_at_root_and_index_html(server: ThreadingHTTPServer) -> None:
    for path in ("/", "/index.html"):
        status, headers, body = _request(server, path)
        assert status == 200, path
        assert headers["content-type"] == "text/html; charset=utf-8"
        assert int(headers["content-length"]) == len(body)
        html = body.decode("utf-8")
        assert "<title>Clousight Bench</title>" in html
        assert '<div id="root">' in html
        # the Vite build references its hashed bundle relative to index.html
        assert 'src="./assets/index-' in html


def test_index_is_strict_csp_compatible(server: ThreadingHTTPServer) -> None:
    """script-src 'self': no inline script bodies, no style= attributes in the shell."""
    _, _, body = _request(server, "/")
    html = body.decode("utf-8")
    for match in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.DOTALL):
        attrs, script_body = match.groups()
        assert "src=" in attrs, "every <script> must load via src (CSP: script-src 'self')"
        assert script_body.strip() == "", "no inline script bodies allowed"
    assert "style=" not in html


def test_head_returns_headers_without_body(server: ThreadingHTTPServer) -> None:
    status, headers, body = _request(server, "/", method="HEAD")
    assert status == 200
    assert body == b""
    assert int(headers["content-length"]) > 0


# ---------------------------------------------------------------------------
# Static assets (strict dist serving)
# ---------------------------------------------------------------------------


def test_hashed_assets_served_with_expected_content_types(server: ThreadingHTTPServer) -> None:
    for suffix, content_type in (
        (".js", "text/javascript; charset=utf-8"),
        (".css", "text/css; charset=utf-8"),
    ):
        path = _hashed_asset_url(suffix)
        status, headers, body = _request(server, path)
        assert status == 200, path
        assert headers["content-type"] == content_type, path
        assert int(headers["content-length"]) == len(body)
        assert len(body) > 0, path


def test_served_asset_bytes_match_packaged_dist(server: ThreadingHTTPServer) -> None:
    path = _hashed_asset_url(".js")
    dist = resource_files("clousight_bench.resources").joinpath("viewer", "dist")
    expected = dist.joinpath("assets").joinpath(path.rsplit("/", 1)[1]).read_bytes()
    _, _, body = _request(server, path)
    assert body == expected


def test_traversal_and_unknown_static_paths_are_json_404(server: ThreadingHTTPServer) -> None:
    """No path escapes dist: `..`/empty segments and unknown files/extensions are JSON 404s."""
    for path in (
        "/assets/../secrets",  # literal .. segment
        "/assets/..%2fsecrets",  # encoded slash decodes inside one segment
        "/..%2f..%2fetc%2fpasswd.json",  # known extension but traversal segments
        "/assets/%2e%2e/secrets.js",  # encoded dot-dot segment
        "/assets/other.js",  # known extension, file absent from dist
        "/assets/",  # empty trailing segment
        "/assets",  # extension-less path (no SPA fallback: hash router)
        "/app.js",  # absent from dist root
        "/index.html.bak",  # extension outside the content-type map
        "/pyproject.toml",  # never serve repo files
    ):
        status, headers, body = _request(server, path)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


def test_colon_segment_is_json_404(server: ThreadingHTTPServer) -> None:
    """`:` in any segment is rejected BEFORE joining.

    On Windows, joining a segment like ``c:secrets.js`` onto a base path
    produces a drive-relative path that escapes the dist tree entirely
    (``PurePath("dist") / "c:x"`` -> ``c:x``), so colons must never reach the
    filesystem — including percent-encoded ones that decode inside a segment.
    """
    for path in (
        "/c:secrets.js",  # drive-relative join bypass at dist root
        "/assets/c:..%5cboot.ini.js",  # drive-relative + encoded backslash
        "/assets/c%3asecrets.js",  # percent-encoded colon decodes inside the segment
    ):
        status, headers, body = _request(server, path)
        assert status == 404, path
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert "error" in json.loads(body)


# ---------------------------------------------------------------------------
# Security headers (added centrally: every response kind carries them)
# ---------------------------------------------------------------------------

EXPECTED_SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}


def test_security_headers_on_every_response_kind(server: ThreadingHTTPServer) -> None:
    for path, expected_status in (
        ("/", 200),
        ("/api/records", 200),
        (_hashed_asset_url(".js"), 200),
        ("/no/such/path", 404),
    ):
        status, headers, _ = _request(server, path)
        assert status == expected_status, path
        for name, value in EXPECTED_SECURITY_HEADERS.items():
            assert headers.get(name) == value, f"{path}: {name}"


def test_security_headers_on_host_403(server: ThreadingHTTPServer) -> None:
    status, headers, _ = _request_with_host(server, "/", "evil.example")
    assert status == 403
    for name, value in EXPECTED_SECURITY_HEADERS.items():
        assert headers.get(name) == value, name


# ---------------------------------------------------------------------------
# Host check
# ---------------------------------------------------------------------------


def test_spoofed_or_missing_host_is_403(server: ThreadingHTTPServer) -> None:
    for host_value in ("evil.example", "evil.example:8787", "127.0.0.1.evil.example", None):
        status, headers, body = _request_with_host(server, "/api/records", host_value)
        assert status == 403, host_value
        assert headers["content-type"] == "application/json; charset=utf-8"
        assert json.loads(body) == {"error": "host not allowed"}


def test_allowed_host_variants_are_200(server: ThreadingHTTPServer) -> None:
    port = server.server_address[1]
    for host_value in (
        "127.0.0.1",
        f"127.0.0.1:{port}",
        "localhost",
        f"localhost:{port}",
        f"LocalHost:{port}",  # case-insensitive
        "[::1]",
        f"[::1]:{port}",
    ):
        status, _, _ = _request_with_host(server, "/api/records", host_value)
        assert status == 200, host_value


# ---------------------------------------------------------------------------
# /api/meta
# ---------------------------------------------------------------------------


def test_api_meta_shape(server: ThreadingHTTPServer) -> None:
    import clousight_bench

    status, headers, body = _request(server, "/api/meta")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    meta = json.loads(body)
    assert set(meta) == {"results_dir", "version", "counts"}
    assert meta["results_dir"] == "results"  # basename only — never a filesystem path
    assert "/" not in meta["results_dir"]
    assert meta["version"] == clousight_bench.__version__
    assert meta["counts"] == {"records": 1}


# ---------------------------------------------------------------------------
# serve() lifecycle
# ---------------------------------------------------------------------------


def test_serve_shuts_down_cleanly_on_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []

    class FakeServer:
        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("clousight_bench.viewer.server.create_server", lambda *a, **kw: FakeServer())
    serve(tmp_path, host="127.0.0.1", port=0)  # must not raise
    assert closed == [True]


# ---------------------------------------------------------------------------
# CLI: csbench serve
# ---------------------------------------------------------------------------


def _patch_cli_server(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[tuple[Path, str, int]], list[Any]]:
    """Fake create_server/serve_until_interrupt for `csbench serve` CLI tests."""
    created: list[tuple[Path, str, int]] = []
    served: list[Any] = []

    class FakeBoundServer:
        def __init__(self, host: str, port: int) -> None:
            self.server_address = (host, port)

    def fake_create_server(results_dir: Path, host: str = "127.0.0.1", port: int = 0) -> Any:
        created.append((results_dir, host, port))
        return FakeBoundServer(host, port)

    monkeypatch.setattr("clousight_bench.viewer.server.create_server", fake_create_server)
    monkeypatch.setattr("clousight_bench.viewer.server.serve_until_interrupt", served.append)
    return created, served


def test_cli_serve_defaults_and_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from clousight_bench.cli import main

    created, served = _patch_cli_server(monkeypatch)
    rc = main(["serve"])
    out = capsys.readouterr().out

    assert rc == 0
    assert created == [(Path("results"), "127.0.0.1", 8787)]
    assert len(served) == 1
    assert "viewer: http://127.0.0.1:8787 (results: results)" in out
    assert "Ctrl-C to stop" in out


def test_cli_serve_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from clousight_bench.cli import main

    created, served = _patch_cli_server(monkeypatch)
    rc = main(["serve", "--results", str(tmp_path), "--port", "9123", "--host", "0.0.0.0"])
    out = capsys.readouterr().out

    assert rc == 0
    assert created == [(Path(tmp_path), "0.0.0.0", 9123)]
    assert len(served) == 1
    assert f"http://0.0.0.0:9123 (results: {tmp_path})" in out


def test_cli_serve_port_zero_prints_actual_bound_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--port 0` binds an ephemeral port and the printed URL shows it, not 0.

    Uses the REAL create_server (bind happens before printing); only the
    blocking serve loop is stubbed, closing the socket instead.
    """
    from clousight_bench.cli import main

    bound_ports: list[int] = []

    def fake_serve_until_interrupt(server: ThreadingHTTPServer) -> None:
        bound_ports.append(server.server_address[1])
        server.server_close()

    monkeypatch.setattr("clousight_bench.viewer.server.serve_until_interrupt", fake_serve_until_interrupt)
    rc = main(["serve", "--results", str(tmp_path), "--port", "0"])
    out = capsys.readouterr().out

    assert rc == 0
    assert len(bound_ports) == 1 and bound_ports[0] != 0
    assert f"http://127.0.0.1:{bound_ports[0]} (results: {tmp_path})" in out
    assert ":0 " not in out


def test_host_without_port_fail_closed_on_junk_suffixes():
    """Port must be empty or digits; bracket junk fails closed (review hardening)."""
    from clousight_bench.viewer.server import _host_without_port

    assert _host_without_port("localhost:8787") == "localhost"
    assert _host_without_port("localhost:evil") == ""
    assert _host_without_port("127.0.0.1:80@evil.com") == ""
    assert _host_without_port("[::1]:8080") == "[::1]"
    assert _host_without_port("[::1]evil:80") == ""
    assert _host_without_port("[::1") == ""
