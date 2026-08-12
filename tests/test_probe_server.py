import json
import time
import urllib.error
import urllib.request

from clousight_bench.core.observation import ObservationBundle
from clousight_bench.domains.agent_runtime.probe.jobs import TERMINAL_STATUSES, JobProgress, JobSpec
from clousight_bench.domains.agent_runtime.probe.runner import JobRunner
from clousight_bench.domains.agent_runtime.probe.server import serve


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def _runner():
    def quick(spec, progress_cb):
        progress_cb(JobProgress(phase="done", completed=1, total=1, elapsed_s=0.0), {})
        return ObservationBundle(observations={"ok": True}, series={})

    return JobRunner({"quick": quick})


def test_health_ok():
    srv = serve(_runner(), host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        status, body = _get(base, "/health")
        assert status == 200 and body["ok"] is True
    finally:
        srv.shutdown()


def test_run_job_then_poll_to_completion():
    srv = serve(_runner(), host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        spec = JobSpec(probe="quick", params={}, target_endpoint="http://127.0.0.1:9999")
        status, body = _post(base, "/run-job", spec.to_dict())
        assert status == 200 and body["job_id"].startswith("job-")
        job_id = body["job_id"]
        deadline = time.time() + 5
        rec = None
        while time.time() < deadline:
            _, rec = _get(base, f"/job/{job_id}")
            if rec["status"] in TERMINAL_STATUSES:
                break
            time.sleep(0.02)
        assert rec["status"] == "completed"
        assert rec["observations"]["observations"] == {"ok": True}
    finally:
        srv.shutdown()


def test_unknown_job_returns_404():
    srv = serve(_runner(), host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        try:
            _get(base, "/job/job-nope")
            assert False, "expected HTTP 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
