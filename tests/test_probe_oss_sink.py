import json
import threading

from clousight_bench.domains.agent_runtime.probe.oss_client import InMemoryOssClient
from clousight_bench.domains.agent_runtime.probe.oss_sink import OssChunkSink


def test_rolls_chunks_midrun_and_writes_manifest_on_close():
    c = InMemoryOssClient()
    sink = OssChunkSink(c, "campaign-x/job-y/", chunk_max_records=1000)
    for i in range(2500):
        sink.append("raw", {"i": i})
    # Two full chunks rolled DURING the run (mid-run queryable), before close.
    midrun = c.list_prefix("campaign-x/job-y/raw-")
    assert midrun == ["campaign-x/job-y/raw-0000.jsonl", "campaign-x/job-y/raw-0001.jsonl"]
    manifest = sink.close()
    # Third (partial) chunk flushed on close.
    keys = c.list_prefix("campaign-x/job-y/raw-")
    assert keys[-1] == "campaign-x/job-y/raw-0002.jsonl"
    counts = [ch["records"] for ch in manifest["chunks"] if ch["stream"] == "raw"]
    assert counts == [1000, 1000, 500]
    # Each chunk is valid JSONL with the right line count.
    chunk0 = c.get_object("campaign-x/job-y/raw-0000.jsonl").decode().splitlines()
    assert len(chunk0) == 1000 and json.loads(chunk0[0]) == {"i": 0}
    # Manifest object written under the prefix.
    assert "campaign-x/job-y/manifest.json" in c.list_prefix("campaign-x/job-y/")
    for ch in manifest["chunks"]:
        assert ch["sha256"].startswith("sha256:") and ch["media"] == "application/x-ndjson"


def test_multiple_streams_have_independent_counters():
    c = InMemoryOssClient()
    sink = OssChunkSink(c, "p/", chunk_max_records=2)
    sink.append("series", {"t": 1, "v": 1.0})
    sink.append("spans", {"span_id": "s1"})
    sink.append("series", {"t": 2, "v": 2.0})  # series hits 2 → rolls series-0000
    m = sink.close()
    streams = sorted({ch["stream"] for ch in m["chunks"]})
    assert streams == ["series", "spans"]
    assert "p/series-0000.jsonl" in c.list_prefix("p/")
    assert "p/spans-0000.jsonl" in c.list_prefix("p/")


def test_flush_noop_when_empty():
    c = InMemoryOssClient()
    sink = OssChunkSink(c, "p/")
    sink.flush()  # nothing buffered
    m = sink.close()
    assert m["chunks"] == []
    assert c.list_prefix("p/") == ["p/manifest.json"]


def test_concurrent_appends_no_loss_no_key_collision():
    """16 threads each appending 50 records against chunk_max=5 exercises rapid
    rolling under contention. Assert: (a) no record lost, (b) no OSS key written
    twice (chunk index collision would overwrite a key and corrupt the manifest).
    """
    c = InMemoryOssClient()
    sink = OssChunkSink(c, "concurrent/", chunk_max_records=5)

    records_per_thread = 50
    num_threads = 16
    total_expected = records_per_thread * num_threads

    def worker(thread_id: int) -> None:
        for i in range(records_per_thread):
            sink.append("raw", {"thread": thread_id, "i": i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    manifest = sink.close()

    # (a) No records lost: sum of all chunk record counts == total appended.
    raw_chunks = [ch for ch in manifest["chunks"] if ch["stream"] == "raw"]
    total_in_chunks = sum(ch["records"] for ch in raw_chunks)
    assert total_in_chunks == total_expected, f"Record loss: expected {total_expected}, got {total_in_chunks}"

    # (b) No chunk key collision: each key in the manifest must be unique,
    # and each key must correspond to exactly one OSS object (no overwrite).
    chunk_keys = [ch["key"] for ch in raw_chunks]
    assert len(chunk_keys) == len(set(chunk_keys)), "Duplicate chunk keys in manifest"

    # Cross-check: every manifest key is present in OSS and vice-versa (no phantom writes).
    oss_raw_keys = c.list_prefix("concurrent/raw-")
    assert sorted(chunk_keys) == sorted(oss_raw_keys), "Manifest keys don't match OSS keys"

    # Verify total records accessible from OSS objects match expected.
    oss_total = sum(len(c.get_object(key).decode().splitlines()) for key in oss_raw_keys)
    assert oss_total == total_expected, (
        f"OSS record count mismatch: expected {total_expected}, got {oss_total}"
    )
