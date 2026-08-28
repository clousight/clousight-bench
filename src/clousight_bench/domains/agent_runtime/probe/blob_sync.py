"""Control-plane side of channel ②: pull a blob-store prefix into a local results dir.

csbench never issues data-plane load, but it does read the probe's telemetry —
by syncing the blob-store prefix into the results tree so the existing query/
trace/rollup tooling works against local files, mid-run or after. This module is
pure control-plane: it only reads the blob store and writes local files.
"""

from __future__ import annotations

import json
from pathlib import Path

from clousight_bench.core.blobstore import BlobStore


def sync_prefix(client: BlobStore, prefix: str, dest_dir: str | Path) -> list[Path]:
    """Mirror every object under ``prefix`` into ``dest_dir`` (relative layout)."""
    dest = Path(dest_dir)
    written: list[Path] = []
    for key in client.list_prefix(prefix):
        rel = key[len(prefix) :] if key.startswith(prefix) else key
        if not rel:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(client.get_object(key))
        written.append(target)
    return sorted(written)


def chunks_to_artifacts(manifest: dict, bucket: str, scheme: str) -> list[dict]:
    """Map a sink manifest to ObservationBundle.artifacts entries.

    Deliberate seam, not dead code: this is the unwired final step of the
    blob-chunk path. ``BlobChunkSink.close()`` writes the manifest and
    ``JobRunner`` (see ``runner.py``) currently lifts only ``chunk_refs`` from
    it. The intended consumer is the control-plane result assembly, which would
    call this to promote the chunk manifest into full ``artifacts`` records on
    the reconstructed :class:`ObservationBundle`.

    Artifact URIs carry the provider's blob-store scheme (aliyun → ``oss``,
    aws → ``s3``), supplied explicitly by the caller. The URI is informational
    in artifact records — nothing downstream parses the scheme back out.
    """
    out: list[dict] = []
    for ch in manifest.get("chunks", []):
        out.append(
            {
                "kind": f"probe-{ch['stream']}",
                "media": ch["media"],
                "sha256": ch["sha256"],
                "uri": f"{scheme}://{bucket}/{ch['key']}",
            }
        )
    return out


def regroup_spans_to_traces(dest_dir: str | Path) -> list[Path]:
    """Turn synced ``spans-*.jsonl`` chunks into per-trace ``traces/<id>.jsonl``."""
    dest = Path(dest_dir)
    grouped: dict[str, list[dict]] = {}
    for chunk in sorted(dest.glob("spans-*.jsonl")):
        for line in chunk.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            tid = str(rec.get("trace_id") or "unknown")
            grouped.setdefault(tid, []).append(rec)
    traces_dir = dest / "traces"
    written: list[Path] = []
    for tid, recs in grouped.items():
        traces_dir.mkdir(parents=True, exist_ok=True)
        path = traces_dir / f"{tid}.jsonl"
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return sorted(written)
