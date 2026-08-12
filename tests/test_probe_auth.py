"""The cb-probe HTTP surface requires a bearer token when one is configured.

/health stays open (preflight reachability); /run-job and /job require the token.
A token-less server stays open (local-sim / tests).
"""

import json
import urllib.error
import urllib.request

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.probe.jobs import JobProgress, JobSpec
from clousight_bench.domains.agent_runtime.probe.runner import JobRunner
from clousight_bench.domains.agent_runtime.probe.server import serve

TOKEN = "s3cret-probe-token"


def _runner():
    def quick(spec, progress_cb):
        progress_cb(JobProgress(phase="done", completed=1, total=1, elapsed_s=0.0), {})
        return ObservationBundle(observations={"ok": True}, series={})

    return JobRunner({"quick": quick})


def _req(base, path, method="GET", auth=None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if auth is not None:
        headers["Authorization"] = auth
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_token_server_gates_run_job_and_job_but_not_health():
    srv = serve(_runner(), host="127.0.0.1", port=0, token=TOKEN)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    spec = JobSpec(probe="quick", params={}, target_endpoint="http://127.0.0.1:9999").to_dict()
    try:
        # health is open
        assert _req(base, "/health") == 200
        # no auth -> 401
        assert _req(base, "/run-job", "POST", payload=spec) == 401
        assert _req(base, "/job/job-xyz") == 401
        # wrong token -> 401
        assert _req(base, "/run-job", "POST", auth="Bearer nope", payload=spec) == 401
        # correct token -> allowed
        assert _req(base, "/run-job", "POST", auth=f"Bearer {TOKEN}", payload=spec) == 200
    finally:
        srv.shutdown()


def test_tokenless_server_stays_open():
    srv = serve(_runner(), host="127.0.0.1", port=0)  # no token
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    spec = JobSpec(probe="quick", params={}, target_endpoint="http://127.0.0.1:9999").to_dict()
    try:
        assert _req(base, "/run-job", "POST", payload=spec) == 200
    finally:
        srv.shutdown()
