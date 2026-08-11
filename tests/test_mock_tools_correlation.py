import json
import urllib.error
import urllib.request
from threading import Thread

from clousight_bench.domains.agent_runtime.mock_tools import make_server


def _get(base, path, corr=None):
    h = {"X-Clousight-Correlation-Id": corr} if corr else {}
    req = urllib.request.Request(base + path, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_correlation_id_buckets_counts_and_faults():
    srv, state = make_server(0)
    Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _post(base, "/fault/config", {"target": "prices", "fail_on_calls": [1], "status": 500, "corr": "A"})
        assert _get(base, "/prices", corr="A")[0] == 500
        assert _get(base, "/prices", corr="B")[0] == 200
        _, st = _get(base, "/fault/state")
        assert st["call_counts"]["prices|A"] == 1
        assert st["call_counts"]["prices|B"] == 1
    finally:
        srv.shutdown()


def test_no_corr_preserves_global_bucket():
    srv, state = make_server(0)
    Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _post(base, "/fault/config", {"target": "prices", "fail_on_calls": [2], "status": 503})
        assert _get(base, "/prices")[0] == 200
        assert _get(base, "/prices")[0] == 503
    finally:
        srv.shutdown()
