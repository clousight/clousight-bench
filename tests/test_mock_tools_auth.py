"""P2-7: optional shared-secret auth on the mock tool server.

In real mode the pinned tool universe must be reachable FROM the cloud runtime,
i.e. exposed on a public tunnel -- an unauthenticated HTTP surface. An optional
``X-Clousight-Token`` lets the operator lock it down: with a token set, every
tool call must present it (health stays open for reachability probes). With no
token set, the server is open exactly as before, so local-sim is unaffected.
"""

import json
from urllib import request
from urllib.error import HTTPError

import pytest

from clousight_bench.domains.agent_runtime.mock_tools import make_server


@pytest.fixture
def serve():
    servers = []

    def _start(token=None):
        from threading import Thread

        server, state = make_server(0, token=token)
        Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for s in servers:
        s.shutdown()


def _get(url, headers=None):
    req = request.Request(url, headers=headers or {})
    with request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_open_server_serves_without_a_token(serve):
    base = serve(token=None)
    status, body = _get(f"{base}/prices")
    assert status == 200
    assert "products" in body


def test_token_required_when_configured(serve):
    base = serve(token="s3cret")
    with pytest.raises(HTTPError) as exc:
        _get(f"{base}/prices")
    assert exc.value.code == 401


def test_correct_token_is_accepted(serve):
    base = serve(token="s3cret")
    status, body = _get(f"{base}/prices", headers={"X-Clousight-Token": "s3cret"})
    assert status == 200


def test_health_stays_open_for_reachability_probe(serve):
    base = serve(token="s3cret")
    status, body = _get(f"{base}/health")  # no token
    assert status == 200
    assert body["ok"] is True
