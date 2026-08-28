# tests/test_probe_sink_wired.py
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.domains.agent_runtime.probe.blob_sink import BlobChunkSink
from clousight_bench.domains.agent_runtime.probe.jobs import JobSpec
from clousight_bench.domains.agent_runtime.probe.server import build_default_runner


class _FakeAgent(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = json.dumps(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": json.dumps({"ok": True, "status": 200})}}
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _serve():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeAgent)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _poll_terminal(runner, job_id, tries=200):
    import time

    for _ in range(tries):
        rec = runner.get(job_id)
        if rec is not None and rec.status in ("completed", "failed"):
            return rec
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_soak_flushes_raw_chunks_to_store_and_reports_chunk_refs():
    srv, base = _serve()
    store = InMemoryBlobStore()
    # sink factory keyed off the JobSpec's blob_prefix, small chunks to force rolls
    factory = lambda spec: BlobChunkSink(store, spec.blob_prefix, chunk_max_records=5)
    runner = build_default_runner(sink_factory=factory)
    try:
        spec = JobSpec(
            probe="soak",
            params={"duration_s": 0.4},
            target_endpoint=base,
            mock_base_url="http://mock",
            blob_prefix="campaign-x/job-y/",
        )
        job_id = runner.submit(spec)
        rec = _poll_terminal(runner, job_id)
    finally:
        srv.shutdown()
    assert rec.status == "completed"
    # raw chunks landed in the blob store (mid-run queryable) + a manifest was written on close
    raw = store.list_prefix("campaign-x/job-y/raw-")
    assert raw, "expected at least one raw chunk flushed to the blob store"
    assert "campaign-x/job-y/manifest.json" in store.list_prefix("campaign-x/job-y/")
    # the poll record surfaces the chunk keys as chunk_refs
    assert rec.chunk_refs and all(r.startswith("campaign-x/job-y/") for r in rec.chunk_refs)


def test_no_sink_factory_keeps_plan2_behavior():
    srv, base = _serve()
    runner = build_default_runner()  # no sink_factory
    try:
        spec = JobSpec(
            probe="soak",
            params={"duration_s": 0.2},
            target_endpoint=base,
            mock_base_url="http://mock",
            blob_prefix="",
        )
        job_id = runner.submit(spec)
        rec = _poll_terminal(runner, job_id)
    finally:
        srv.shutdown()
    assert rec.status == "completed"
    assert rec.chunk_refs == []  # no sink → no chunk refs
