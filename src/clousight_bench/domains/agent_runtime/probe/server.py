"""The cb-probe HTTP surface: /health, /run-job, /job/<id>.

A thin adapter over JobRunner. Async by construction: /run-job returns a job_id
immediately, /job/<id> is polled by csbench. No streaming back to the client.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .dataplane import (
    run_cancellation,
    run_concurrency_ceiling,
    run_fault_recovery,
    run_hol_blocking,
    run_idle_timeout_honor,
    run_rate_limit,
    run_retry_storm,
    run_scaling,
    run_soak,
    run_startup_curve,
    run_sustained_load,
    run_ttft,
    run_warm_retention,
)
from .jobs import JobSpec
from .runner import JobRunner


def make_handler(runner: JobRunner, token: str | None = None) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            # No token configured -> open (local-sim / tests). Token set -> every
            # request except /health must present `Authorization: Bearer <token>`.
            # The probe binds 0.0.0.0 on a public IP, so this is its only gate.
            if not token:
                return True
            import hmac

            presented = (self.headers.get("Authorization") or "").removeprefix("Bearer ")
            return hmac.compare_digest(presented, token)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if self.path.startswith("/job/"):
                job_id = self.path[len("/job/") :]
                rec = runner.get(job_id)
                if rec is None:
                    self._send(404, {"error": f"no job {job_id}"})
                else:
                    self._send(200, rec.to_dict())
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/run-job":
                self._send(404, {"error": "not found"})
                return
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            n = int(self.headers.get("Content-Length", 0))
            spec = JobSpec.from_dict(json.loads(self.rfile.read(n) or b"{}"))
            try:
                job_id = runner.submit(spec)
            except KeyError as exc:
                self._send(400, {"error": str(exc)})
                return
            self._send(200, {"job_id": job_id})

        def log_message(self, *a):
            pass

    return _Handler


def serve(
    runner: JobRunner, host: str = "0.0.0.0", port: int = 0, token: str | None = None
) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(runner, token))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def build_default_runner(sink_factory=None) -> JobRunner:
    from clousight_bench.domains.agent_runtime.dataplane_dispatch import _assert_conforms

    probes = {
        "ttft": run_ttft,
        "sustained_load": run_sustained_load,
        "soak": run_soak,
        "warm_retention": run_warm_retention,
        "rate_limit": run_rate_limit,
        "concurrency_ceiling": run_concurrency_ceiling,
        "cancellation": run_cancellation,
        "scaling": run_scaling,
        "hol_blocking": run_hol_blocking,
        "fault_recovery": run_fault_recovery,
        "retry_storm": run_retry_storm,
        "startup_curve": run_startup_curve,
        "idle_timeout_honor": run_idle_timeout_honor,
    }
    # Drift guard: the remote probe registry MUST cover exactly the canonical
    # PROBE_NAMES that csbench's scorers expect. A silent rename here would
    # otherwise change scoring output with no test failing — see A1 in the audit.
    _assert_conforms(set(probes), who="cb-probe build_default_runner")
    return JobRunner(probes, sink_factory=sink_factory)


def main() -> None:
    port = int(os.environ.get("PORT", "9000"))
    token = os.environ.get("CB_PROBE_TOKEN") or None
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(build_default_runner(), token))
    srv.serve_forever()


if __name__ == "__main__":
    main()
