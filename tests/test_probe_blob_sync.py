from clousight_bench.core.blobstore import InMemoryBlobStore
from clousight_bench.domains.agent_runtime.probe.blob_sink import BlobChunkSink
from clousight_bench.domains.agent_runtime.probe.blob_sync import regroup_spans_to_traces, sync_prefix


def test_sync_mirrors_prefix_into_dest(tmp_path):
    c = InMemoryBlobStore()
    sink = BlobChunkSink(c, "campaign-x/job-y/", chunk_max_records=2)
    for i in range(3):
        sink.append("raw", {"i": i})
    sink.close()
    dest = tmp_path / "results"
    written = sync_prefix(c, "campaign-x/job-y/", dest)
    names = sorted(p.name for p in written)
    assert names == ["manifest.json", "raw-0000.jsonl", "raw-0001.jsonl"]
    assert (dest / "raw-0000.jsonl").read_text().splitlines() != []
    # Round-trip: synced bytes equal the stored object bytes.
    assert (dest / "raw-0000.jsonl").read_bytes() == c.get_object("campaign-x/job-y/raw-0000.jsonl")


def test_sync_is_idempotent_and_can_run_midrun(tmp_path):
    c = InMemoryBlobStore()
    sink = BlobChunkSink(c, "p/", chunk_max_records=1)
    sink.append("raw", {"i": 0})  # rolls p/raw-0000.jsonl immediately
    dest = tmp_path / "r"
    first = sync_prefix(c, "p/", dest)  # mid-run sync: 1 chunk present, no manifest yet
    assert [p.name for p in first] == ["raw-0000.jsonl"]
    sink.append("raw", {"i": 1})  # rolls p/raw-0001.jsonl
    sink.close()
    second = sync_prefix(c, "p/", dest)  # now 2 chunks + manifest
    assert sorted(p.name for p in second) == ["manifest.json", "raw-0000.jsonl", "raw-0001.jsonl"]


def _write_spans_chunk(client, key, records):
    import json

    client.put_object(key, ("\n".join(json.dumps(r) for r in records) + "\n").encode())


def test_regroup_spans_groups_by_trace_id_and_is_found_by_traceview(tmp_path):
    c = InMemoryBlobStore()
    _write_spans_chunk(
        c,
        "p/spans-0000.jsonl",
        [
            {"trace_id": "T1", "span_id": "a", "parent_span_id": "", "name": "chain", "kind": "CHAIN"},
            {"trace_id": "T2", "span_id": "b", "parent_span_id": "", "name": "chain", "kind": "CHAIN"},
        ],
    )
    _write_spans_chunk(
        c,
        "p/spans-0001.jsonl",
        [
            {"trace_id": "T1", "span_id": "c", "parent_span_id": "a", "name": "llm", "kind": "LLM"},
        ],
    )
    dest = tmp_path / "results"
    sync_prefix(c, "p/", dest)
    written = regroup_spans_to_traces(dest)
    names = sorted(p.name for p in written)
    assert names == ["T1.jsonl", "T2.jsonl"]
    # T1 has both of its spans (across two chunks).
    t1_lines = (dest / "traces" / "T1.jsonl").read_text().splitlines()
    assert len(t1_lines) == 2
    # traceview finds the trace by id (filename stem match).
    from clousight_bench.core.traceview import find_trace

    spans = find_trace(dest, "T1")
    assert spans is not None and len(spans) == 2
