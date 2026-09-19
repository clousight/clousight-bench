"""The write gate: what `csbench serve` will and will not let a browser do.

The viewer's security argument has always been "it only reads". `--allow-write`
does not replace that argument — it keeps it as the default. Every test here
exists to keep one sentence true: **with no flag, this server behaves exactly
as it did before targets could be written.**
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from clousight_bench.viewer.server import create_server

WRITE_HEADER = "X-Csbench-Write"


def _serve(server: ThreadingHTTPServer) -> Iterator[ThreadingHTTPServer]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def configs(tmp_path: Path) -> Path:
    d = tmp_path / "configs"
    d.mkdir()
    (d / "duckdb-sf1.yaml").write_text("target:\n  mode: mock\nparams:\n  scale_factor: 1\n")
    return d


@pytest.fixture()
def readonly(tmp_path: Path, configs: Path) -> Iterator[ThreadingHTTPServer]:
    (tmp_path / "results").mkdir(exist_ok=True)
    yield from _serve(create_server(tmp_path / "results", host="127.0.0.1", port=0, configs_dir=configs))


@pytest.fixture()
def writable(tmp_path: Path, configs: Path) -> Iterator[ThreadingHTTPServer]:
    (tmp_path / "results").mkdir(exist_ok=True)
    yield from _serve(
        create_server(tmp_path / "results", host="127.0.0.1", port=0, configs_dir=configs, allow_write=True)
    )


def _call(
    srv: ThreadingHTTPServer,
    method: str,
    path: str,
    *,
    body: object = None,
    header: str | None = "1",
    raw: bytes | None = None,
) -> tuple[int, dict]:
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        headers = {"Content-Type": "application/json"}
        if header is not None:
            headers[WRITE_HEADER] = header
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        text = resp.read()
        try:
            return resp.status, json.loads(text)
        except json.JSONDecodeError:
            return resp.status, {"raw": text.decode(errors="replace")}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reading is not gated
# ---------------------------------------------------------------------------


def test_targets_are_readable_without_the_flag(readonly: ThreadingHTTPServer) -> None:
    """The Targets page must be useful on a read-only server."""
    status, body = _call(readonly, "GET", "/api/targets", header=None)
    assert status == 200
    assert [t["name"] for t in body["targets"]] == ["duckdb-sf1"]

    status, body = _call(readonly, "GET", "/api/targets/duckdb-sf1", header=None)
    assert status == 200
    assert body["data"]["params"] == {"scale_factor": 1}


def test_meta_says_whether_writing_is_on(
    readonly: ThreadingHTTPServer, writable: ThreadingHTTPServer
) -> None:
    # The UI must not offer a button the server will answer 405 to.
    assert _call(readonly, "GET", "/api/meta", header=None)[1]["write_enabled"] is False
    assert _call(writable, "GET", "/api/meta", header=None)[1]["write_enabled"] is True


def test_an_unknown_target_reads_404_not_500(readonly: ThreadingHTTPServer) -> None:
    assert _call(readonly, "GET", "/api/targets/nope", header=None)[0] == 404
    assert _call(readonly, "GET", "/api/targets/..", header=None)[0] == 404
    assert _call(readonly, "GET", "/api/targets/%2e%2e%2fetc%2fpasswd", header=None)[0] == 404


# ---------------------------------------------------------------------------
# Writing is gated three times over
# ---------------------------------------------------------------------------


def test_without_the_flag_a_write_is_405_and_says_how(readonly: ThreadingHTTPServer, configs: Path) -> None:
    status, body = _call(readonly, "PUT", "/api/targets/new", body={"yaml": "target: {}\n"})
    assert status == 405
    assert "--allow-write" in body["error"]
    assert not (configs / "new.yaml").exists()

    assert _call(readonly, "DELETE", "/api/targets/duckdb-sf1")[0] == 405
    assert (configs / "duckdb-sf1.yaml").exists()


def test_without_the_custom_header_a_write_is_403(writable: ThreadingHTTPServer, configs: Path) -> None:
    # No cross-origin <form> can set a custom header, which is the CSRF shape
    # that actually threatens a server bound to localhost.
    status, body = _call(writable, "PUT", "/api/targets/new", body={"yaml": "target: {}\n"}, header=None)
    assert status == 403
    assert WRITE_HEADER in body["error"]
    assert not (configs / "new.yaml").exists()


def test_an_oversized_body_is_refused_before_it_is_read(writable: ThreadingHTTPServer, configs: Path) -> None:
    status, _ = _call(writable, "PUT", "/api/targets/big", raw=b"x" * (64 * 1024 + 1))
    assert status == 413
    assert not (configs / "big.yaml").exists()


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


def test_a_new_target_is_written(writable: ThreadingHTTPServer, configs: Path) -> None:
    text = "target:\n  mode: mock\nparams: {}\n"
    status, body = _call(writable, "PUT", "/api/targets/fresh", body={"yaml": text})
    assert status == 200
    assert body["name"] == "fresh"
    assert (configs / "fresh.yaml").read_text() == text


def test_overwriting_takes_an_explicit_yes(writable: ThreadingHTTPServer, configs: Path) -> None:
    before = (configs / "duckdb-sf1.yaml").read_text()
    status, body = _call(writable, "PUT", "/api/targets/duckdb-sf1", body={"yaml": "target: {}\n"})
    assert status == 409
    assert (configs / "duckdb-sf1.yaml").read_text() == before

    status, _ = _call(
        writable, "PUT", "/api/targets/duckdb-sf1", body={"yaml": "target: {}\n", "overwrite": True}
    )
    assert status == 200
    assert (configs / "duckdb-sf1.yaml").read_text() == "target: {}\n"


def test_unparsable_yaml_never_reaches_the_disk(writable: ThreadingHTTPServer, configs: Path) -> None:
    status, body = _call(writable, "PUT", "/api/targets/bad", body={"yaml": "target: [unclosed\n"})
    assert status == 400
    assert "YAML" in body["error"]
    assert not (configs / "bad.yaml").exists()


def test_a_target_must_be_a_mapping(writable: ThreadingHTTPServer, configs: Path) -> None:
    status, _ = _call(writable, "PUT", "/api/targets/listy", body={"yaml": "- a\n- b\n"})
    assert status == 400
    assert not (configs / "listy.yaml").exists()


def test_a_redaction_placeholder_is_refused_rather_than_saved(
    writable: ThreadingHTTPServer, configs: Path
) -> None:
    """Round-tripping a redacted read must not overwrite the real credential."""
    status, body = _call(
        writable, "PUT", "/api/targets/creds", body={"yaml": "target:\n  api_token: '***'\n"}
    )
    assert status == 400
    assert "***" in body["error"]
    assert not (configs / "creds.yaml").exists()


def test_a_forbidden_name_is_404_and_leaks_no_path(writable: ThreadingHTTPServer, tmp_path: Path) -> None:
    status, body = _call(writable, "PUT", "/api/targets/%2e%2e%2fescape", body={"yaml": "a: 1\n"})
    assert status == 404
    assert str(tmp_path) not in json.dumps(body)
    assert not (tmp_path / "escape.yaml").exists()


def test_a_body_that_is_not_json_is_400(writable: ThreadingHTTPServer) -> None:
    assert _call(writable, "PUT", "/api/targets/x", raw=b"not json")[0] == 400


def test_a_body_without_yaml_is_400(writable: ThreadingHTTPServer) -> None:
    assert _call(writable, "PUT", "/api/targets/x", body={"overwrite": True})[0] == 400


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_the_file(writable: ThreadingHTTPServer, configs: Path) -> None:
    status, body = _call(writable, "DELETE", "/api/targets/duckdb-sf1")
    assert status == 200
    assert body["name"] == "duckdb-sf1"
    assert not (configs / "duckdb-sf1.yaml").exists()


def test_deleting_what_is_not_there_is_404(writable: ThreadingHTTPServer) -> None:
    assert _call(writable, "DELETE", "/api/targets/ghost")[0] == 404


def test_results_are_never_writable(writable: ThreadingHTTPServer) -> None:
    """The sealed records have no write route at all, flag or no flag."""
    for method in ("PUT", "DELETE", "POST"):
        status, _ = _call(writable, method, "/api/record/run-abc123", body={"status": "completed"})
        assert status == 404


# ---------------------------------------------------------------------------
# The flag has to survive the trip from the command line
# ---------------------------------------------------------------------------


def test_serve_defaults_to_read_only(monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path) -> None:
    seen = _record_create_server(monkeypatch)
    from clousight_bench.cli.app import main

    main(["serve", "--results", str(tmp_path), "--port", "0"])
    assert seen["allow_write"] is False
    assert "read-only" in capsys.readouterr().out


def test_serve_allow_write_reaches_the_server(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    seen = _record_create_server(monkeypatch)
    from clousight_bench.cli.app import main

    main(["serve", "--results", str(tmp_path), "--port", "0", "--allow-write", "--configs", "cfg"])
    assert seen["allow_write"] is True
    assert seen["configs_dir"] == Path("cfg")
    # The posture is printed: scrollback should say which server is running.
    assert "read+write" in capsys.readouterr().out


def _record_create_server(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Capture create_server's kwargs; serve nothing."""
    seen: dict = {}

    class _FakeServer:
        server_address = ("127.0.0.1", 9999)

    def _fake_create(results_dir, host="127.0.0.1", port=0, *, configs_dir=None, allow_write=False):
        seen.update(
            results_dir=results_dir, host=host, port=port, configs_dir=configs_dir, allow_write=allow_write
        )
        return _FakeServer()

    from clousight_bench.viewer import server as server_mod

    monkeypatch.setattr(server_mod, "create_server", _fake_create)
    monkeypatch.setattr(server_mod, "serve_until_interrupt", lambda srv: None)
    return seen
