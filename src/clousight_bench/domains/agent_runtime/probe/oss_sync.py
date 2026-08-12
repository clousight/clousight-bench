"""Control-plane side of channel ②: pull an OSS prefix into a local results dir.

csbench never issues data-plane load, but it does read the probe's telemetry —
by syncing the OSS prefix into the results tree so the existing query/trace/
rollup tooling works against local files, mid-run or after. This module is pure
control-plane: it only reads OSS and writes local files.
"""

from __future__ import annotations

import json
from pathlib import Path

from .oss_client import OssClient


def sync_prefix(client: OssClient, prefix: str, dest_dir: str | Path) -> list[Path]:
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


def chunks_to_artifacts(manifest: dict, bucket: str) -> list[dict]:
    """Map a sink manifest to ObservationBundle.artifacts entries (oss:// uris)."""
    out: list[dict] = []
    for ch in manifest.get("chunks", []):
        out.append(
            {
                "kind": f"probe-{ch['stream']}",
                "media": ch["media"],
                "sha256": ch["sha256"],
                "uri": f"oss://{bucket}/{ch['key']}",
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
